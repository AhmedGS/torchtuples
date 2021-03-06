
from collections import OrderedDict
import pytest
import numpy as np
import pandas as pd
import torch
from torchtuples import optim, Model, tuplefy
import torchtuples.callbacks as cb

class MocModel:
    def __init__(self, optimizer, batches_per_epoch=None):
        self.optimizer = optimizer
        self.fit_info = dict(batches_per_epoch=batches_per_epoch)


class StopBeforeStep(cb.Callback):
    def before_step(self):
        return True

def _identity_loss(input, target, *args, **kwargs):
    return 1.


class TestCallbackHandler:
    def test_init_multiple_identical(self):
        cbh = cb.CallbackHandler([cb.Callback(), cb.Callback()])
        assert len(cbh) == 2

    def test_append_model(self):
        cbh = cb.CallbackHandler([cb.Callback()])
        cbh.append(cb.Callback())
        assert len(cbh) == 2

    def test_init_naming_distinct(self):
        cbh = cb.CallbackHandler([cb.Callback(), cb.Callback()])
        c = list(cbh.keys())
        assert c[0] != c[1]
        
    def test_append_naming_distinct(self):
        cbh = cb.CallbackHandler([cb.Callback()])
        cbh.append(cb.Callback())
        c = list(cbh.keys())
        assert c[0] != c[1]

    def test_callback_naming_init(self):
        cbs = dict(foo=cb.Callback(), bar=cb.Callback())
        cbh = cb.CallbackHandler(cbs)
        c = list(cbh.keys())
        assert c[0] == 'foo'
        assert c[1] == 'bar'

    def test_callback_naming_append(self):
        cbs = dict(foo=cb.Callback(), bar=cb.Callback())
        cbh = cb.CallbackHandler(cbs)
        cbh.append(cb.Callback(), 'new_foo')
        cbh['new_bar'] = cb.Callback()
        c = list(cbh.keys())
        assert c[0] == 'foo'
        assert c[1] == 'bar'
        assert c[2] == 'new_foo'
        assert c[3] == 'new_bar'
        with pytest.raises(AssertionError) as ex:
            cbh.append(cb.Callback(), 'foo')
        assert str(ex.value) == "Duplicate name: foo"
        with pytest.raises(AssertionError) as ex:
            cbh['bar'] = cb.Callback()
        assert str(ex.value) == "Duplicate name: bar"
        


class TestCallbacksInModel:
    def setup(self):
        torch.manual_seed(1234)
        self.inp, self.tar = torch.randn(10, 3), torch.randn(10)
        self.net = torch.nn.Linear(3, 1)
        self.optim_class = optim.SGD
        self.model = Model(self.net, torch.nn.MSELoss(), self.optim_class(lr=0.1), device='cpu')
        self.model.fit(self.inp, self.tar, epochs=0)

    def test_callback_type(self):
        callbacks = self.model.callbacks
        assert type(callbacks) is cb.TrainingCallbackHandler
        assert type(callbacks.callbacks) is OrderedDict

    def test_callback_order(self):
        callbacks = self.model.callbacks
        keys = list(callbacks.keys())
        vals = list(callbacks.values())
        keys_true = ['optimizer', 'train_metrics', 'val_metrics', 'log']
        vals_types = [optim.SGD, cb._MonitorFitMetricsTrainData, cb.MonitorFitMetrics,
                      cb.TrainingLogger]
        for k, kt in zip(keys, keys_true):
            assert k == kt
        for v, vt in zip(vals, vals_types):
            assert type(v) is vt
        for k in keys:
            assert callbacks[k] is callbacks.callbacks[k]

    def log_at_end(self):
        torch.manual_seed(1234)
        inp, tar = torch.randn(10, 3), torch.randn(10)
        net = torch.nn.Linear(3, 1)
        optim_class = optim.SGD
        self.model = Model(net, torch.nn.MSELoss(), optim_class(lr=0.1), device='cpu')
        self.model.fit(inp, tar, epochs=0, callbacks=[cb.EarlyStopping()])

    def test_multiple_idential_list(self):
        model = Model(self.net, torch.nn.MSELoss(), self.optim_class(lr=0.1), device='cpu')
        cbs = [cb.Callback(), cb.Callback(), cb.Callback()]
        self.model.fit(self.inp, self.tar, epochs=0, callbacks=cbs)
        callbacks = self.model.callbacks
        keys = list(callbacks.keys())
        vals = list(callbacks.values())
        keys_true = ['optimizer', 'train_metrics', 'val_metrics', 'Callback', 'Callback_0',
                     'Callback_1', 'log']
        vals_types = [optim.SGD, cb._MonitorFitMetricsTrainData, cb.MonitorFitMetrics,
                      cb.Callback, cb.Callback, cb.Callback, cb.TrainingLogger]
        assert len(callbacks) == 7
        for k, kt in zip(keys, keys_true):
            assert k == kt
        for v, vt in zip(vals, vals_types):
            assert type(v) is vt
        for k in keys:
            assert callbacks[k] is callbacks.callbacks[k]

class TestDecoupledWeightDecay:
    @pytest.mark.parametrize('optim_class', [
        optim.SGD,
        optim.RMSprop,
        optim.Adam,
        optim.AdamW,
        optim.AdamWR,
    ])
    @pytest.mark.parametrize('wd', [0.1, 0.01, 0.001])
    def test_decoupled_weight_decay(self, optim_class, wd):
        torch.manual_seed(1234)
        inp = torch.randn(10, 3)
        net = torch.nn.Linear(3, 1)
        weight = net.weight.clone().data
        weight_decay = cb.DecoupledWeightDecay(wd)
        op = optim_class()(net.parameters())
        model = MocModel(op)
        weight_decay.give_model(model)
        net(inp).mean().backward()
        weight_decay.before_step()
        assert (net.weight.data == (weight - wd * weight)).all()

    @pytest.mark.parametrize('optim_class', [
        optim.SGD,
        optim.RMSprop,
        optim.Adam,
        optim.AdamW,
        optim.AdamWR,
    ])
    @pytest.mark.parametrize('wd', [0.1, 0.01, 0.001])
    @pytest.mark.parametrize('nb_epochs', [2, 3])
    def test_decoupled_weight_decay_normalized(self, optim_class, wd, nb_epochs):
        import math
        torch.manual_seed(1234)
        inp = torch.randn(10, 3)
        net = torch.nn.Linear(3, 1)
        weight = net.weight.clone().data
        weight_decay = cb.DecoupledWeightDecay(wd, True, nb_epochs)
        op = optim_class()(net.parameters())
        batches_per_epoch = 2
        model = MocModel(op, batches_per_epoch)
        weight_decay.give_model(model)
        weight_decay.on_fit_start()
        net(inp).mean().backward()
        weight_decay.before_step()
        wd = wd * math.sqrt(1 / (nb_epochs * batches_per_epoch))
        assert (net.weight.data == (weight - wd * weight)).all()

    @pytest.mark.parametrize('optim_class', [
        optim.SGD,
        optim.RMSprop,
        optim.Adam,
        optim.AdamW,
        optim.AdamWR,
    ])
    @pytest.mark.parametrize('wd', [0.1, 0.01, 0.001])
    def test_decoupled_weight_decay_with_model(self, optim_class, wd):
        torch.manual_seed(1234)
        inp, tar = torch.randn(10, 3), torch.randn(10, 1)
        net = torch.nn.Linear(3, 1)
        weight = net.weight.clone().data
        weight_decay = cb.DecoupledWeightDecay(wd)
        model = Model(net, torch.nn.MSELoss(), optim_class(0.1), device='cpu')
        model.fit(inp, tar, callbacks=[weight_decay, StopBeforeStep()])
        assert (net.weight.data == (weight - wd * weight)).all()

    @pytest.mark.parametrize('optim_class', [
        optim.SGD,
        optim.RMSprop,
        optim.Adam,
        optim.AdamW,
        optim.AdamWR,
    ])
    @pytest.mark.parametrize('wd', [0.1, 0.01, 0.001])
    @pytest.mark.parametrize('nb_epochs', [2, 3])
    def test_decoupled_weight_decay_normalized_with_model(self, optim_class, wd, nb_epochs):
        import math
        torch.manual_seed(1234)
        inp, tar = torch.randn(10, 3), torch.randn(10, 1)
        net = torch.nn.Linear(3, 1)
        weight = net.weight.clone().data
        weight_decay = cb.DecoupledWeightDecay(wd, True, nb_epochs)
        batch_size = 2
        model = Model(net, torch.nn.MSELoss(), optim_class(0.1), device='cpu')
        model.fit(inp, tar, batch_size, callbacks=[weight_decay, StopBeforeStep()])
        batches_per_epoch = model.fit_info['batches_per_epoch']
        wd = wd * math.sqrt(1 / (nb_epochs * batches_per_epoch))
        assert (net.weight.data == (weight - wd * weight)).all()


class TestMonitorMetrics:
    def test_append_score(self):
        mm = cb.MonitorMetrics()
        mm.on_epoch_end()
        mm.append_score('foo', 1)
        mm.on_epoch_end()
        mm.append_score('foo', 5)
        mm.append_score('bar', 2)
        mm.on_epoch_end()
        mm.append_score('bar', 4)
        assert mm.scores['foo']['score'] == [1, 5]
        assert mm.scores['foo']['epoch'] == [0, 1]
        assert mm.scores['bar']['score'] == [2, 4]
        assert mm.scores['bar']['epoch'] == [1, 2]

    def test_append_score_if_epoch_every(self):
        mm = cb.MonitorMetrics()
        mm.on_epoch_end()
        mm.append_score_if_epoch('foo', 1)
        mm.on_epoch_end()
        mm.append_score_if_epoch('foo', 5)
        mm.append_score_if_epoch('bar', 2)
        mm.on_epoch_end()
        mm.append_score_if_epoch('bar', 4)
        assert mm.scores['foo']['score'] == [1, 5]
        assert mm.scores['foo']['epoch'] == [0, 1]
        assert mm.scores['bar']['score'] == [2, 4]
        assert mm.scores['bar']['epoch'] == [1, 2]

    def test_append_score_if_epoch_2(self):
        mm = cb.MonitorMetrics(per_epoch=2)
        mm.on_epoch_end() # add
        mm.append_score_if_epoch('foo', 1) 
        mm.on_epoch_end() # not
        mm.append_score_if_epoch('foo', 5)
        mm.append_score_if_epoch('bar', 2)
        mm.on_epoch_end() # add
        mm.append_score_if_epoch('bar', 4)
        mm.on_epoch_end() # not
        mm.append_score_if_epoch('bar', 8)
        mm.on_epoch_end() # add
        mm.append_score_if_epoch('bar', 9)
        assert mm.scores['foo']['score'] == [1]
        assert mm.scores['foo']['epoch'] == [0]
        assert mm.scores['bar']['score'] == [4, 9]
        assert mm.scores['bar']['epoch'] == [2, 4]

    def test_to_pandas(self):
        mm = cb.MonitorMetrics()
        mm.on_epoch_end()
        mm.append_score('foo', 1)
        mm.on_epoch_end()
        mm.append_score('foo', 5)
        mm.append_score('bar', 2)
        mm.on_epoch_end()
        mm.append_score('bar', 4)
        df = mm.to_pandas()
        assert type(df) is pd.DataFrame
        df_true = pd.DataFrame(dict(foo=[1., 5., None], bar=[None, 2., 4.]))
        pd.testing.assert_frame_equal(df, df_true)

class TestMonitoFitMetrics:
    def setup(self):
        torch.manual_seed(1234)
        self.inp, self.tar = torch.randn(10, 3), torch.randn(10)
        self.net = torch.nn.Linear(3, 1)
        self.optim_class = optim.SGD
        self.model = Model(self.net, _identity_loss, self.optim_class(lr=0.1), device='cpu')
        self.model.fit(self.inp, self.tar, epochs=0)

    def test_add_nans(self):
        mm = cb.MonitorFitMetrics()
        mm.give_model(self.model)
        mm.on_epoch_end()
        mm.on_epoch_end()
        assert mm.scores == {'loss': {'epoch': [0, 1], 'score': [np.nan, np.nan]}}

    def test_add_identity(self):
        dl = tuplefy(self.inp, self.tar).make_dataloader(10, False)
        mm = cb.MonitorFitMetrics(dl)
        mm.give_model(self.model)
        mm.on_epoch_end()
        mm.on_epoch_end()
        assert mm.scores == {'loss': {'epoch': [0, 1], 'score': [1.0, 1.0]}}

    def test_add_identity_2(self):
        dl = tuplefy(self.inp, self.tar).make_dataloader(10, False)
        mm = cb.MonitorFitMetrics(dl, 2)
        mm.give_model(self.model)
        for _ in range(5):
            mm.on_epoch_end()
        assert mm.scores == {'loss': {'epoch': [0, 2, 4], 'score': [1.0, 1.0, 1.0]}}
