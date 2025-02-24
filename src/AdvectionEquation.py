import numpy as np
from clawpack import pyclaw, riemann

from configs.Adv2dConfig import Adv2dModelConfig

class Advection2D():
    def __init__(self, config:Adv2dModelConfig):
        self.nx = config.nx
        self.ny = config.ny
        self.num_eqn = config.num_eqn
        
        self.x_domain = config.x_domain
        self.y_domain = config.y_domain
        
        self.x_velocity = config.x_velocity
        self.y_velocity = config.y_velocity
        
        self.t_final = config.t_final
        self.n_steps = config.n_steps
        
        self.x = pyclaw.Dimension(self.x_domain[0], self.x_domain[1], self.nx, name='x')
        self.y = pyclaw.Dimension(self.y_domain[0], self.y_domain[1], self.ny, name='y')
        
        self.domain = pyclaw.Domain([self.x, self.y])
        state = pyclaw.State(self.domain, self.num_eqn)
        self.X, self.Y = state.grid.p_centers
        
        self.dimensional_split = config.dimensional_split
        self.transverse_waves = config.transverse_waves
        
    
    def get_solver(self):
        solver = pyclaw.ClawSolver2D(riemann.advection_2D)
        solver.dimensional_split = self.dimensional_split
        solver.transverse_waves = self.transverse_waves
        solver.limiters = pyclaw.limiters.tvd.vanleer
        
        solver.bc_lower[0] = pyclaw.BC.periodic
        solver.bc_upper[0] = pyclaw.BC.periodic
        solver.bc_lower[1] = pyclaw.BC.periodic
        solver.bc_upper[1] = pyclaw.BC.periodic
        
        # Assumes classic solver, NOT sharpclaw
        if not self.dimensional_split and not self.transverse_waves:
            solver.cfl_max = 0.5
            solver.cfl_desired = 0.45
        else:
            solver.cfl_max = 1.0
            solver.cfl_desired = 0.9
            
        return solver
        
    
    def step(self, current_state):
        solver = self.get_solver()
        state = pyclaw.State(self.domain, self.num_eqn)
        state.q[0,:,:] = current_state
        
        state.problem_data['u'] = self.x_velocity
        state.problem_data['v'] = self.y_velocity
        
        claw = pyclaw.Controller()
        claw.solution = pyclaw.Solution(state, self.domain)
        claw.solver = solver
        claw.output_format = None
        claw.keep_copy = True
        claw.tfinal = self.t_final
        claw.num_output_times = self.n_steps
        claw.verbosity = 0
        claw.check_validity()
        claw.run()
        
        return claw.frames
        
        
    
    def initial_condition(self):
        """Set initial condition for q.
        Sample scalar equation with data that is piecewise constant with
        q = 1.0  if  0.1 < x < 0.6   and   0.1 < y < 0.6
            0.1  otherwise
        """
        return 0.9*(0.1<self.X)*(self.X<0.6)*(0.1<self.Y)*(self.Y<0.6) + 0.1