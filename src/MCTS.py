from src.OED import OED, OEDGymConfig
import copy
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy  as np

class MCTSConfig():
    def __init__(self):
        self.max_node = 1000
        self.num_layers = 10
        self.hidden_size = 100
        self.gamma = 1 #discount factor
        self.prior_score_scale_factor = 1

class MLPNetwork(nn.Module):
    def __init__(self, nx, ny, num_actions, num_layers, hidden_size):
        super(MLPNetwork, self).__init__()
        input_size = nx * ny

        # Build the common network using nn.Sequential.
        layers = []
        # First layer: from input_size to hidden_size.
        layers.append(nn.Linear(input_size, hidden_size))
        layers.append(nn.ReLU())
        # Add additional hidden layers if num_layers > 1.
        for _ in range(num_layers - 1):
            layers.append(nn.Linear(hidden_size, hidden_size))
            layers.append(nn.ReLU())

        self.common = nn.Sequential(*layers)

        # Value head: outputs a single scalar.
        self.value_head = nn.Linear(hidden_size, 1)
        # Policy head: outputs logits for each action.
        self.policy_head = nn.Linear(hidden_size, num_actions)

    def forward(self, x):
        # Flatten the grid input.
        x = x.view(x.size(0), -1)
        common_features = self.common(x)

        # Compute value head.
        value = self.value_head(common_features)

        # Compute policy head and apply softmax to obtain probabilities.
        policy_logits = self.policy_head(common_features)
        policy = F.softmax(policy_logits, dim=1)

        return value, policy

class node:
    def __init__(self, state, parent, reward):
        #state that the node referencing to
        self.state = copy.deepcopy(state)
        #node can have 1 parent and multiple children
        #root node is the node that has None as parent
        self.parent = parent
        self.children= {}
        #self.children is a dict that point to the childs, with the key is the action that lead to that child (e.g. 8 if action 8)
        #number of time the node has been visited
        self.N = 0
        #state value predicted by the network
        self.V_s = None
        #reward going from parent to this node
        self.R = reward


class MCTS:
    def __init__(self, seed, pde_system, gym_config: OEDGymConfig, mcts_config: MCTSConfig):
        self.env = OED(pde_system, gym_config)
        self.seed = seed
        #a mlp network to learn V value and policy distribution
        if gym_config.old_action_space:
            self.action_space = gym_config.n_sensor * (pde_system.nx * pde_system.ny - gym_config.n_sensor)
        else:
            self.action_space = 4 * gym_config.n_sensor
        self.network = MLPNetwork(pde_system.nx, pde_system.ny, self.action_space, mcts_config.num_layers, mcts_config.hidden_size)

        # max node to search
        self.max_node = mcts_config.max_node
        # max depth of the tree before exploring another branch from current root
        # will be set at each step before calling MCTS search, to simulate reaching the end of episode
        self.max_depth = None
        #discount factor
        self.gamma = mcts_config.gamma
        #scale down prior_score
        self.prior_scale = mcts_config.prior_score_scale_factor

        #keep track of node explored
        self.node_explored = 0
        #current root node
        self.root = None

    def train(self,total_timestep= 50000):
        # total_timestep is the time that this function call env.step(), this does not include iterations in tree
        env_state, info = self.env.reset(seed=self.seed)
        learning_step = 0
        step_since_start_episode = 0
        while learning_step < total_timestep:
            # set the max_depth of tree here, so MCTS only search to the end of the episode
            self.max_depth = self.env.max_horizon - step_since_start_episode
            # choose the best next action
            self.network.eval() # turn network to eval mode
            best_action, action_probs = self.search(env_state)
            #todo: some data structure to store env_state and action_probs and update reward till the end of episode

            # step the env according to best action
            env_state, reward, done, truncated, info = self.env.step(best_action)
            learning_step += 1
            step_since_start_episode += 1
            if done:
                env_state, info = self.env.reset(seed=self.seed + learning_step)
                step_since_start_episode = 0
                # each time the self.best_action is called, we have to wait till the end of the episode to see all the cumulative reward of that action
                # the network will learn to match that env_state to action_probs and cumulative rewards
                self.network.train() #turn network to train mode to learn here
                #todo: training code for neural network
        #save the trained model and the optimizer statedict

    @torch.no_grad()
    def expand(self, current_node, parent_depth):
        """This function accepts a parent node, and expand to best child according to policy"""

        current_depth = parent_depth
        while True:
            state_value, next_state_prior = self.network(
                torch.tensor(current_node.state, dtype=torch.float32).unsqueeze(0)
            )

            # a leaf is the node that just got visited (no backprop yet to increase N)
            is_leaf = (current_node.N == 0)
            # return the leaf node so begin backprop back to root node before next expansion
            if is_leaf:
                # only set node value if new node, otherwise this already contained information from previous backprop
                current_node.V_s = float(state_value)
                # increase node explored count
                self.node_explored += 1
                return current_node

            current_depth += 1

            if current_depth < self.max_depth:
                # call the network, get the value of the parent node, and get next state
                # get all the next state
                next_states, rewards = zip(*[
                    self.env.update_state_and_reward(current_node.state, a)
                    for a in range(self.action_space)
                ])
                # retrieve value of next states, if the children state is not explored yet, next_state_V = 0 for that action
                next_state_V = np.array([
                    current_node.children[a].V_s if a in current_node.children.keys() else 0
                    for a in range(self.action_space)
                ])
                # Q_score for the action
                Q_score = np.array(rewards) + self.gamma * next_state_V
                # prior_score = child_prior * sqrt(parent_visit) / (child_visit + 1)
                child_N = np.array([
                    current_node.children[a].N if a in current_node.children.keys() else 0
                    for a in range(self.action_space)
                ])
                prior_score = np.array(next_state_prior).flatten() + np.sqrt(current_node.N) / (child_N + 1)
                UCB_scores = Q_score + self.prior_scale * prior_score
                best_action = np.argmax(UCB_scores)
                # add child to parent children tracking dict
                if best_action in current_node.children.keys():
                    # if node is explored before, reference it
                    best_child = current_node.children[best_action]
                else:
                    # create and store
                    best_child = node(next_states[best_action], current_node, rewards[best_action])
                    current_node.children[best_action] = best_child
                # Update current_node to best_child and continue the loop
                current_node = best_child
            else:
                # if maximum depth reached, return current_node
                return current_node

    def backpropagation(self, current_node):
        while True:
            # Increment visit count of the current node.
            current_node.N += 1

            # If we've reached the root (no parent), stop the loop.
            if current_node.parent is None:
                break

            # one can think of current_node.R is the instant reward to go from parent node to current node
            # and current_node.V_s, which is predicted by the neural network, to be the cumulative rewards till the end of the epsiode
            # So we do backprop by back up parent_node.V_s using incremental monte carlo formular
            # Compute the backup value G for the parent using the current node's reward and estimated value.
            G = current_node.R + self.gamma * current_node.V_s

            # Update the parent's value estimate using the incremental Monte Carlo formula.
            # Note: parent's N is not incremented until the next loop iteration, hence N + 1 here
            current_node.parent.V_s = current_node.parent.V_s + 1.0 / (current_node.parent.N + 1) * (G - current_node.parent.V_s)

            # Move up to the parent node to continue backpropagation.
            current_node = current_node.parent

    def search(self, env_state):
        #set root node
        self.root = node(env_state, None, 0) #reward at root node doesn't matter, hence set to 0
        self.node_explored = 0
        while self.node_explored < self.max_node:
            #expand the root node, until leaf node or max depth is reached
            current_node = self.expand(self.root, 0)
            # recursively backprob at leaf node, all the way to root node
            self.backpropagation(current_node)
        #choose best action based on frequency visit at root node
        root_N = np.array([self.root.children[a].N if a in self.root.children else 0 for a in range(self.action_space)])
        mcts_action_probs = root_N / root_N.sum()
        best_action = np.argmax(mcts_action_probs)
        return best_action, mcts_action_probs