import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as fnn
from torch import optim

# optimization helper class
class OPT_helper():
    def __init__(self,scanner,spins,NN,nmb_total_samples_dataset):
        self.globepoch = 0
        self.subjidx = 0
        self.nmb_total_samples_dataset = nmb_total_samples_dataset
        
        self.use_periodic_grad_moms_cap = 1
        self.learning_rate = 0.02
        
        self.opti_mode = 'seq'
        
        self.scanner = scanner
        self.spins = spins
        self.NN = NN
        
        self.optimizer = None
        self.init_variables = None
        self.phi_FRP_model = None
        
        self.scanner_opt_params = None
        self.aux_params = None
        self.opt_param_idx = []
        
    def set_handles(self,init_variables,phi_FRP_model):
        self.init_variables = init_variables
        self.phi_FRP_model = phi_FRP_model
        
    def set_opt_param_idx(self,opt_param_idx):
        self.opt_param_idx  = opt_param_idx
        
    def new_batch(self):
        self.globepoch += 1
        
        if hasattr(self.spins, 'batch_size'):
            batch_size = self.spins.batch_size
        else:
            batch_size = 1
        
        self.subjidx = np.random.choice(self.nmb_total_samples_dataset, batch_size, replace=False)
        #self.subjidx = np.random.choice(batch_size, batch_size, replace=False)
        
    def weak_closure(self):
        self.optimizer.zero_grad()
        loss,_,_ = self.phi_FRP_model(self.scanner_opt_params, self.aux_params)
        loss.backward()
        
        return loss        
        
    def train_model(self, training_iter = 100):

        self.aux_params = [self.use_periodic_grad_moms_cap, self.opti_mode]
        WEIGHT_DECAY = 1e-8
        
        # only optimize a subset of params
        optimizable_params = []
        for i in range(len(self.opt_param_idx)):
            optimizable_params.append(self.scanner_opt_params[self.opt_param_idx[i]])
        
        if self.opti_mode == 'seq':
            self.optimizer = optim.Adam(optimizable_params, lr=self.learning_rate, weight_decay=WEIGHT_DECAY)
        elif self.opti_mode == 'nn':
            self.optimizer = optim.Adam(list(self.NN.parameters()), lr=self.learning_rate, weight_decay=WEIGHT_DECAY)
        elif self.opti_mode == 'seqnn':
            self.optimizer = optim.Adam(list(self.NN.parameters())+optimizable_params, lr=self.learning_rate, weight_decay=WEIGHT_DECAY)
            
        for inner_iter in range(training_iter):
            self.new_batch()
            self.optimizer.step(self.weak_closure)
            
            _,reco,error = self.phi_FRP_model(self.scanner_opt_params, self.aux_params)
            print("recon error = %f" %error)
        
    def train_model_with_restarts(self, nmb_rnd_restart=15, training_iter=10):
        
        # init gradients and flip events
        nmb_outer_iter = nmb_rnd_restart
        nmb_inner_iter = training_iter
        
        self.aux_params = [self.use_periodic_grad_moms_cap, self.opti_mode]
        WEIGHT_DECAY = 1e-8
        
        best_error = 1000
        
        for outer_iter in range(nmb_outer_iter):
            #print('restarting... %i%% ready' %(100*outer_iter/float(nmb_outer_iter)))
            print('restarting... ')
            
            self.scanner_opt_params = self.init_variables()
            
            # only optimize a subset of params
            optimizable_params = []
            for i in range(len(self.opt_param_idx)):
                optimizable_params.append(self.scanner_opt_params[self.opt_param_idx[i]])              
                
            if self.opti_mode == 'seq':
                self.optimizer = optim.Adam(optimizable_params, lr=self.learning_rate, weight_decay=WEIGHT_DECAY)
            elif self.opti_mode == 'nn':
                self.optimizer = optim.Adam(list(self.NN.parameters()), lr=self.learning_rate, weight_decay=WEIGHT_DECAY)
            elif self.opti_mode == 'seqnn':
                self.optimizer = optim.Adam(list(self.NN.parameters())+optimizable_params, lr=self.learning_rate, weight_decay=WEIGHT_DECAY)
            
            for inner_iter in range(nmb_inner_iter):
                self.new_batch()
                self.optimizer.step(self.weak_closure)
                
                _,reco,error = self.phi_FRP_model(self.scanner_opt_params, self.aux_params)
                
                if error < best_error:
                    print("recon error = %f" %error)
                    best_error = error
                    
                    best_vars = []
                    for par in self.scanner_opt_params:
                        best_vars.append(par.detach().clone())
                        
        for pidx in range(len(self.scanner_opt_params)):
            self.scanner_opt_params[pidx] = best_vars[pidx]
            self.scanner_opt_params[pidx].requires_grad = True
                                   
                                   