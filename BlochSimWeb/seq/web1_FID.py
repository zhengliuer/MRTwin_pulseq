experiment_id = 'web1_FID'

# %% S0. SETUP env
import sys,os
os.chdir(os.path.abspath(os.path.dirname(__file__)))  #  makes the ex folder your working directory
sys.path.append(os.path.dirname(os.getcwd()))         #  add required folders to path
mpath=os.path.dirname(os.getcwd())
c1=r'codes'; c2=r'codes\GradOpt_python'; c3=r'codes\scannerloop_libs' #  add required folders to path
sys.path += [rf'{mpath}\{c1}',rf'{mpath}\{c2}',rf'{mpath}\{c3}']

## imports for simulation
from GradOpt_python.pulseq_sim_external import sim_external
from GradOpt_python.new_core.util import plot_kspace_trajectory
import math
import numpy as np
import torch
from matplotlib import pyplot as plt

## imports for pypulseq
from pypulseq.Sequence.sequence import Sequence
from pypulseq.calc_duration import calc_duration
from pypulseq.make_adc import make_adc
from pypulseq.make_delay import make_delay
from pypulseq.make_sinc_pulse import make_sinc_pulse
from pypulseq.make_trap_pulse import make_trapezoid
from pypulseq.make_block_pulse import make_block_pulse
from pypulseq.make_extended_trapezoid import make_extended_trapezoid
from pypulseq.make_arbitrary_grad import make_arbitrary_grad
from pypulseq.opts import Opts


# %% S1. SETUP sys

## choose the scanner limits
system = Opts(max_grad=28, grad_unit='mT/m', max_slew=150, slew_unit='T/m/s', rf_ringdown_time=20e-6,
                rf_dead_time=100e-6, adc_dead_time=20e-6,grad_raster_time=50*10e-6)

# %% S2. DEFINE the sequence 
seq = Sequence()

# Define FOV and resolution
fov = 1000e-3 
Nread = 128
Nphase = 1
slice_thickness = 8e-3  # slice


# Define other gradients and ADC events
adc = make_adc(num_samples=Nread, duration=2e-3, phase_offset=0*np.pi/180,delay=0,system=system)


# ======
# CONSTRUCT SEQUENCE
# ======
for i in range(0,12):
    
    rf1, _ = make_block_pulse(flip_angle=(i+1)*30 * math.pi / 180, duration=1e-3,system=system)

    seq.add_block(rf1)
    seq.add_block(adc)
    seq.add_block(make_delay(2e-3))




# %% S3. CHECK, PLOT and WRITE the sequence  as .seq
ok, error_report = seq.check_timing()  # Check whether the timing of the sequence is correct
if ok:
    print('Timing check passed successfully')
else:
    print('Timing check failed. Error listing follows:')
    [print(e) for e in error_report]

# PLOT sequence
sp_adc,t_adc =seq.plot(clear=False)
#   
if 0:
    sp_adc,t_adc =seq.plot(clear=True)


# Prepare the sequence output for the scanner
seq.set_definition('FOV', [fov, fov, slice_thickness])
seq.set_definition('Name', 'gre')
seq.write('out/external.seq')
seq.write('out/' + experiment_id +'.seq')


# %% S4: SETUP SPIN SYSTEM/object on which we can run the MR sequence external.seq from above
from new_core.sim_data import SimData
sz=[64,64]
# (i) load a phantom object from file
if 0:
    obj_p = SimData.load('../data/phantom2D.mat')
    obj_p = SimData.load('../data/numerical_brain_cropped.mat')
    obj_p = obj_p.resize(sz[0],sz[1],1)
else:
# or (ii) set phantom  manually to a pixel phantom
    obj_p = torch.zeros((sz[0],sz[1],6)); 
    
    obj_p[24,24,:]=torch.tensor([1, 3, 0.5, 30e-3, 0, 1]) # dimensions: PD, T1 T2, T2dash, dB0 rB1
    # obj_p[7,23:29,:]=torch.tensor([1, 1, 0.1,0.1, 0, 1]) # dimensions: PD, T1 T2,T2dash, dB0 rB1
    
    obj_p=obj_p.permute(2,0,1)[:,:,:,None]
    obj_p= SimData(obj_p[0,:],obj_p[1,:],obj_p[2,:],obj_p[3,:],obj_p[4,:],obj_p[5,:]*torch.ones(1,obj_p.shape[1],obj_p.shape[2],1),torch.ones(1,obj_p.shape[1],obj_p.shape[2],1),normalize_B0_B1= False)
    obj_p = obj_p.resize(sz[0],sz[1],1)
    
# manipulate obj and plot it
obj_p.B0*=1;
obj_p.plot_sim_data()


# %% S5:. SIMULATE  the external.seq file and add acquired signal to ADC plot
signal, _= sim_external(obj=obj_p,plot_seq_k=[0,0])
# plot the result into the ADC subplot
sp_adc.plot(t_adc,np.real(signal.numpy()),t_adc,np.imag(signal.numpy()))
sp_adc.plot(t_adc,np.abs(signal.numpy()))
# seq.plot(signal=signal.numpy())



#%%  FITTING BLOCK - work in progress
from scipy import optimize

# choose echo tops and flatten extra dimensions
S=signal[63::128,:].abs().ravel()
t=t_adc[63::128].ravel()

S=S.numpy()
def fit_func(t, a, R,c):
    return a*np.exp(-R*t) + c   

p=optimize.curve_fit(fit_func,t,S,p0=(1, 1,0))
print(p[0][1])

fig=plt.figure("""fit""")
ax1=plt.subplot(131)
ax=plt.plot(t_adc,np.abs(signal.numpy()),label='fulldata')
ax=plt.plot(t,S,'x',label='data')
plt.plot(t,fit_func(t,p[0][0],p[0][1],p[0][2]),label="f={:.2}*exp(-{:.2}*t)+{:.2}".format(p[0][0], p[0][1],p[0][2]))
plt.title('fit')
plt.legend()
plt.ion()

plt.show()