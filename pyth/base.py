
'''Base models.
'''
from collections import OrderedDict
import numpy as np
import torch
from torch import optim
from torch.utils.data import TensorDataset
from .data import DataLoaderSlice, DatasetTuple
from . import callbacks as cb

class Model(object):
    '''Abstract base model.

    Parameters:
        net: Pytorch Module.
        optimizer: Torch optimizer. If None Adam with default.
        device: Which device to compute on.
            Preferrably pass a torch.device object.
            If `None`: use default gpu if avaiable, else use cpu.
            If `int`: used that gpu: torch.device('cuda:<device>').
            If `string`: string is passed to torch.device(`string`).
    '''
    def __init__(self, net, loss=None, optimizer=None, device=None, net_predict=None):
        self.net = net
        self.loss = loss
        self.optimizer = optimizer if optimizer else optim.Adam(self.net.parameters())

        self.device = self._device_from__init__(device)
        self.net.to(self.device)
        self.net_predict = net_predict if net_predict else self.net
        self.net_predict.to(self.device)

        self.train_loss = cb.MonitorTrainLoss()
        self.log = cb.TrainingLogger()
        self.log.monitors = OrderedDict(train_loss=self.train_loss)
    
    @staticmethod
    def _device_from__init__(device):
        if device is None:
            device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        elif device.__class__ is str:
            device = torch.device(device)
        elif device.__class__ is int:
            device = torch.device('cuda:{}'.format(device))
        else:
            if device.__class__ is not torch.device:
                raise ValueError('Argument `device` needs to be None, string, or torch.device object.')
        return device
    
    @property
    def optimizer(self):
        return self._optimizer

    @optimizer.setter
    def optimizer(self, optimizer):
        self._optimizer = optimizer
    
    @property
    def net_predict(self):
        return self._net_predict
    
    @net_predict.setter
    def net_predict(self, net_predict):
        self._net_predict = net_predict
    
    def _setup_train_info(self, dataloader, verbose, callbacks):
        self.fit_info = {'batches_per_epoch': len(dataloader)}

        self.log.verbose = verbose
        if callbacks is None:
            callbacks = []
        self.callbacks = cb.CallbacksList([self.train_loss] + callbacks + [self.log])
        self.callbacks.give_model(self)

    def fit_dataloader(self, dataloader, epochs=1, callbacks=None, verbose=True):
        self._setup_train_info(dataloader, verbose, callbacks)
        self.callbacks.on_fit_start()
        for _ in range(epochs):
            for data in dataloader:
                self.optimizer.zero_grad()
                self.batch_loss = self.compute_loss(data)
                self.batch_loss.backward()
                stop_signal = self.callbacks.before_step()
                if stop_signal:
                    raise RuntimeError('Stop signal in before_step().')
                self.optimizer.step()
                self.callbacks.on_batch_end()
            stop_signal = self.callbacks.on_epoch_end()
            if stop_signal:
                break
        return self.log
    
    def fit_tensor(self, input, target, batch_size=256, epochs=1, callbacks=None, verbose=True,
                    num_workers=0):
        """Fit  model with inputs and targets.
        
        Arguments:
            input {tensor or tuple} -- Input (x) passed to net.
            target {tensor or tuple} -- Target (y) passed to loss function.
        
        Keyword Arguments:
            batch_size {int} -- Elemets in each batch (default: {256})
            epochs {int} -- Number of epochs (default: {1})
            callbacks {list} -- list of callbacks (default: {None})
            verbose {bool} -- Print progress (default: {True})
            num_workers {int} -- Number of workers used in the dataloader (default: {0})
        """
        # if input.__class__ is torch.Tensor:
        #     input = (input,)
        # if target.__class__ is torch.Tensor:
        #     target = (target,)
        # dataset = DatasetTuple(input, target)
        # dataloader = DataLoaderSlice(dataset, batch_size, shuffle=True, num_workers=num_workers)
        dataloader = tensor_to_dataloader(input, target, batch_size, shuffle=True,
                                          num_workers=num_workers)
        return self.fit_dataloader(dataloader, epochs, callbacks, verbose)

    def fit_numpy(self, input, target, batch_size=256, epochs=1, callbacks=None, verbose=True,
                  num_workers=0):
        """Fit model with inputs and targets.
        
        Arguments:
            input {array or tuple} -- Input (x) passed to net.
            target {array or tuple} -- Target (y) passed to loss function.
        
        Keyword Arguments:
            batch_size {int} -- Elemets in each batch (default: {256})
            epochs {int} -- Number of epochs (default: {1})
            callbacks {list} -- list of callbacks (default: {None})
            verbose {bool} -- Print progress (default: {True})
            num_workers {int} -- Number of workers used in the dataloader (default: {0})
        """
        input = numpy_to_tensor(input)
        target = numpy_to_tensor(target)
        return self.fit_tensor(input, target, batch_size, epochs, callbacks, verbose, num_workers)
    
    def score_in_batches(self, data, score_func=None, batch_size=1028, eval_=True, mean=True,
                         num_workers=0, shuffle=False):
        if data.__class__ not in (list, tuple):
            return self.score_in_batches_dataloader(data, score_func, eval_, mean)
        input, target = data
        object_class = _nested_unpack_class(input)
        if object_class is torch.Tensor:
            return self.score_in_batches_tensor(input, target, score_func, batch_size,
                                                eval_, mean, num_workers, shuffle)
        elif object_class is np.ndarray:
            return self.score_in_batches_numpy(input, target, score_func, batch_size,
                                                eval_, mean, num_workers, shuffle)
        raise ValueError("Need `data` to be a dataloader or contain np.arrays or torch tensors.")
    
    def score_in_batches_numpy(self, input, target, score_func=None, batch_size=1028,
                                eval_=True, mean=True, num_workers=0, shuffle=False):
        input, target = numpy_to_tensor((input, target))
        return self.score_in_batches_tensor(input, target,score_func, batch_size,
                                            eval_, mean, num_workers, shuffle)

    def score_in_batches_tensor(self, input, target, score_func=None, batch_size=1028,
                                eval_=True, mean=True, num_workers=0, shuffle=False):
        dataloader = tensor_to_dataloader(input, target, batch_size, shuffle=shuffle,
                                          num_workers=num_workers)
        return self.score_in_batches_dataloader(dataloader, score_func, eval_, mean)
    
    def score_in_batches_dataloader(self, dataloader, score_func=None, eval_=True, mean=True):
        '''Score a dataset in batches.

        Parameters:
            dataloader: Dataloader:
            score_func: Function of (self, data) that returns a measure.
                If None, we get training loss.
            eval_: If net should be in eval mode.
            mean: If return mean or list with scores.
        '''
        if eval_:
            self.net.eval()
        batch_scores = []
        with torch.no_grad():
            for data in dataloader:
                if score_func is None:
                    score = self.compute_loss(data)
                else:
                    score = score_func(self, data)
                batch_scores.append(score)
        if eval_:
            self.net.train()
        if mean:
            batch_scores = [score.item() for score in batch_scores]
            return np.mean(batch_scores)
        return batch_scores

    def compute_loss(self, data):
        '''Example loss function for x,y dataloaders'''
        input, target = data
        input = self._to_device(input)
        target = self._to_device(target)
        out = self.net(*input)
        if out.__class__ is torch.Tensor:
            out = [out]
        return self.loss(*out, *target)
    
    def _to_device(self, x):
        if x.__class__ is torch.Tensor:
            x = [x.to(self.device)]
        else:
            x = [sub.to(self.device) for sub in x]
        return x
            
    @property
    def loss(self):
        return self._loss
    
    @loss.setter
    def loss(self, loss):
        if loss is None:
            loss = NotImplemented
        self._loss = loss
    
    def predict_func_dataloader(self, dataloader, func=None, return_numpy=True, eval_=True, grads=False, move_to_cpu=False):
        '''Get func(X) for dataloader.

        Parameters:
            dataloader: Pytorch dataloader.
            func: Pytorch module.
            return_numpy: If False, a torch tensor is returned.
            eval_: If true, set `fun` in eval mode for prediction
                and back to train mode after that (only affects dropout and batchnorm).
                If False, leaves `fun` modes as they are.
            grads: If gradients should be computed.
            move_to_cpu: For large data set we want to keep as torch.Tensors we need to
                move them to the cpu.
        '''
        #########################3
        # Need to fix this so it understands which part of dataloader is x and y
        ######################
        if func is None:
            func = self.net_predict
        if eval_:
            func.eval()
        with torch.set_grad_enabled(grads):
            # preds = [func(*self._to_device(x)) for x in iter(dataloader)]
            preds = [self._predict_move_between_devices(func, x, return_numpy, move_to_cpu) 
                     for x in dataloader]
        if eval_:
            func.train()
        
        if preds[0].__class__ is torch.Tensor:
            preds = torch.cat(preds)
        else:
            preds = [torch.cat(sub) for sub in (zip(*preds))]

        if return_numpy:
            if preds.__class__ is torch.Tensor:
                return preds.numpy()
            else:
                return [sub.numpy() for sub in preds]
        return preds
    
    def _predict_move_between_devices(self, func, x, return_numpy, move_to_cpu):
        preds = func(*self._to_device(x))
        if return_numpy or move_to_cpu:
            if preds.__class__ is torch.Tensor:
                preds = preds.cpu()
            else:
                return [sub.cpu() for sub in preds]
        return preds

    def predict_func_tensor(self, x, func=None, batch_size=8224, return_numpy=False, eval_=True, grads=False, move_to_cpu=False):
        '''Get func(X) for a tensor (or list of tensors) x.

        Parameters:
            x: Tensor or list of tensors with covariates.
            func: Pytorch module.
            batch_size: Batch size.
            return_numpy: If False, a torch tensor is returned.
            eval_: If true, set `fun` in eval mode for prediction
                and back to train mode after that (only affects dropout and batchnorm).
                If False, leaves `fun` modes as they are.
            move_to_cpu: For large data set we want to keep as torch.Tensors we need to
                move them to the cpu.
        '''
        dataset = TensorDataset(*[x])
        dataloader = DataLoaderSlice(dataset, batch_size)
        return self.predict_func_dataloader(dataloader, func, return_numpy, eval_, grads,
                                            move_to_cpu)

    def predict_func_numpy(self, x, func=None, batch_size=8224, return_numpy=True, eval_=True, grads=False):
        '''Get func(X) for a numpy array x.

        Parameters:
            X: Numpy matrix with with covariates.
            func: Pytorch module.
            batch_size: Batch size.
            return_numpy: If False, a torch tensor is returned.
            eval_: If true, set `fun` in eval mode for prediction
                and back to train mode after that (only affects dropout and batchnorm).
                If False, leaves `fun` modes as they are.
        '''
        dataset = NumpyTensorDataset(*[x])
        dataloader = DataLoaderSlice(dataset, batch_size)
        return self.predict_func_dataloader(dataloader, func, return_numpy, eval_, grads)

    def save_model_weights(self, path, **kwargs):
        '''Save the model weights.

        Parameters:
            path: The filepath of the model.
            **kwargs: Arguments passed to torch.save method.
        '''
        return torch.save(self.net.state_dict(), path, **kwargs)

    def load_model_weights(self, path, **kwargs):
        '''Load model weights.

        Parameters:
            path: The filepath of the model.
            **kwargs: Arguments passed to torch.load method.
        '''
        self.net.load_state_dict(torch.load(path, **kwargs))


def _nested_unpack_class(iterable):
    first = iterable[0]
    if first.__class__ in (list, tuple):
        return _nested_unpack_class(first)
    return first.__class__

def numpy_to_tensor(x):
    if x.__class__ in [list, tuple]:
        return [numpy_to_tensor(sub) for sub in x]
    return torch.from_numpy(x).float()
    
def tensor_to_dataloader(input, target, batch_size, shuffle, num_workers):
    if input.__class__ is torch.Tensor:
        input = (input,)
    if target.__class__ is torch.Tensor:
        target = (target,)
    dataset = DatasetTuple(input, target)
    dataloader = DataLoaderSlice(dataset, batch_size, shuffle=shuffle, num_workers=num_workers)
    return dataloader

#     # def _predict_func_numpy(self, func, X, batch_size=8224, return_numpy=True, eval_=True):
#     #     '''Get func(X) for a numpy array X.

#     #     Parameters:
#     #         func: Pytorch module.
#     #         X: Numpy matrix with with covariates.
#     #         batch_size: Batch size.
#     #         return_numpy: If False, a torch tensor is returned.
#     #         eval_: If true, set `fun` in eval mode for prediction
#     #             and back to train mode after that (only affects dropout and batchnorm).
#     #             If False, leaves `fun` modes as they are.
#     #     '''
#     #     dataset = NumpyTensorDataset(X)
#     #     dataloader = DataLoaderSlice(dataset, batch_size)
#     #     return self._predict_func_dataloader(func, dataloader, return_numpy, eval_)

    
#     # def _predict_func_tensor(self, func, x, return_numpy=True, eval_=True):
#     #     '''Get func(X) for tensor X.

#     #     Parameters:
#     #         func: Pytorch module.
#     #         x: Pytorch tensor.
#     #         return_numpy: If False, a torch tensor is returned.
#     #         eval_: If true, set `fun` in eval mode for prediction
#     #             and back to train mode after that (only affects dropout and batchnorm).
#     #             If False, leaves `fun` modes as they are.
#     #     '''
#     #     if eval_:
#     #         func.eval()
#     #     with torch.no_grad():
#     #         preds = func(x.to(self.device))
#     #     if eval_:
#     #         func.train()

#     #     if return_numpy:
#     #         return preds.numpy()
#     #     return preds






# import torch
# from .fitnet import FitNet

# class FitNetGeneral(FitNet):
#     def fit_dataloader(self, dataloader, epochs=1, callbacks=None, verbose=1):
#     self._setup_train_info(dataloader, verbose, callbacks)
#     self.callbacks.on_fit_start()
#     for _ in range(epochs):
#         for x in dataloader:
#             if x.__class__ is torch.Tensor:
#                 x = x.to(self.device)
#             else:
#                 x = [sub.to(self.device) for sub in x]
#             recon_x, mu, log_var = self.net(x)
#             self.batch_loss = self.loss_func(recon_x, x, mu, log_var)
#             self.optimizer.zero_grad()
#             self.batch_loss.backward()
#             stop_signal = self.callbacks.before_step()
#             if stop_signal:
#                 raise RuntimeError('Stop signal in before_step().')
#             self.optimizer.step()
#             self.callbacks.on_batch_end()
#         stop_signal = self.callbacks.on_epoch_end()
#         if stop_signal:
#             break
#     return self.log

#     def in_loop(self, data):
#         pass