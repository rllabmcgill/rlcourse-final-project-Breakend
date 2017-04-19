from sampling_utils import *
from rllab.sampler.base import BaseSampler
from rllab.misc import tensor_utils
from rllab.policies.uniform_control_policy import UniformControlPolicy
import numpy as np

class Trainer(object):

    def __init__(self, sess, env, cost_approximator, cost_trainer, novice_policy, novice_policy_optimizer, num_frames=4, concat_timesteps=True):
        """
        sess : tensorflow session
        cost_approximator : the NN or whatever cost function that can take in your observations/states and then give you your reward
        cost_trainer : this is the trainer for optimizing the cost (i.e. runs tensorflow training ops, etc.)
        novice_policy : the policy of your novice agent
        novice_policy_optimizer : the optimizer which runs a policy optimization step (or constrained number of iterations)
        much of this can be found in https://github.com/bstadie/third_person_im/blob/master/sandbox/bradly/third_person/algos/cyberpunk_trainer.py#L164
        """
        self.sess = sess
        self.env = env
        self.cost_approximator = cost_approximator
        self.cost_trainer = cost_trainer
        self.iteration = 0
        self.novice_policy = novice_policy
        self.novice_policy_optimizer = novice_policy_optimizer
        # self.sampler = BaseSampler(self.novice_policy_optimizer)
        self.concat_timesteps = concat_timesteps
        self.num_frames = num_frames

        # as in traditional GANs, we add failure noise
        # TODO: what's the most correct way to use this?
        self.noise_fail_policy = UniformControlPolicy(env.spec)

    def step(self, expert_rollouts, expert_horizon=200, dump_datapoints=False, number_of_sample_trajectories=None, config={}):

        if number_of_sample_trajectories is None:
            number_of_sample_trajectories = len(expert_rollouts)


        # This does things like calculate advantages and entropy, etc.
        # if we use the cost function when acquiring the novice rollouts, this will use our cost function
        # for optimizing the trajectories
        # import pdb; pdb.set_trace()
        novice_rollouts = self.novice_policy_optimizer.obtain_samples(self.iteration)
        novice_rollouts = process_samples_with_reward_extractor(novice_rollouts, self.cost_approximator, self.concat_timesteps, self.num_frames)
        policy_training_samples = self.novice_policy_optimizer.process_samples(itr=self.iteration, paths=novice_rollouts)

        # novice_rollouts = sample_policy_trajectories(policy=self.novice_policy, number_of_trajectories=number_of_sample_trajectories, env=self.env, horizon=expert_horizon, reward_extractor=self.cost_approximator, num_frames=self.num_frames, concat_timesteps=self.concat_timesteps)


        print("True Reward: %f" % np.mean([np.sum(p['true_rewards']) for p in novice_rollouts]))
        print("Discriminator Reward: %f" % np.mean([np.sum(p['rewards']) for p in novice_rollouts]))

        # if we're using a cost trainer train it?
        if self.cost_trainer:

            # Novice rollouts gets all the rewards, etc. used for policy optimization, for the cost function we just want to use the observations.
            # use "observations for the observations/states provided by the env, use "im_observations" to use the pixels (if available)
            # import pdb; pdb.set_trace()
            # novice_rollouts_tensor = tensor_utils.concat_tensor_list([path["observations"] for path in novice_rollouts])
            # expert_rollouts_tensor = tensor_utils.concat_tensor_list([path["observations"] for path in expert_rollouts])
            # novice_rollouts_tensor = tensor_utils.stack_tensor_list([p['observations'] for p in novice_rollouts])
            # expert_rollouts_tensor = tensor_utils.stack_tensor_list([p['observations'] for p in expert_rollouts])
            novice_rollouts_tensor = [path["observations"] for path in novice_rollouts]
            novice_rollouts_tensor = tensor_utils.pad_tensor_n(novice_rollouts_tensor, expert_horizon)
            expert_rollouts_tensor = [path["observations"] for path in expert_rollouts]
            expert_rollouts_tensor = tensor_utils.pad_tensor_n(expert_rollouts_tensor, expert_horizon)

            self.cost_trainer.train_cost(novice_rollouts_tensor, expert_rollouts_tensor, number_epochs=2, num_frames=self.num_frames)



        # optimize the novice policy by one step
        # TODO: put this in a config provider or something?
        if "policy_opt_steps_per_global_step" in config:
            policy_opt_epochs = config["policy_opt_steps_per_global_step"]
            learning_schedule = False
        else:
            learning_schedule = False
            policy_opt_epochs = 1

        if "policy_opt_learning_schedule" in config:
            learning_schedule = config["policy_opt_learning_schedule"]

        print("Number of policy opt epochs %d" % policy_opt_epochs)

        if learning_schedule:
            # override the policy epochs for larger number of iterations
            policy_opt_epochs *= 2**int(self.iteration/100)
            policy_opt_epochs = min(policy_opt_epochs, 5)
            print("increasing policy opt epochs to %d" % policy_opt_epochs )

        for i in range(policy_opt_epochs):
            # import pdb; pdb.set_trace()
            if i >= 1:
                # Resample so TRPO doesn't just reject all the steps
                novice_rollouts = self.novice_policy_optimizer.obtain_samples(self.iteration)
                novice_rollouts = process_samples_with_reward_extractor(novice_rollouts, self.cost_approximator, self.concat_timesteps, self.num_frames)
                policy_training_samples = self.novice_policy_optimizer.process_samples(itr=self.iteration, paths=novice_rollouts)

            self.novice_policy_optimizer.optimize_policy(itr=self.iteration, samples_data=policy_training_samples)
            self.iteration += 1

        if dump_datapoints:
            self.cost_trainer.dump_datapoints(self.num_frames)

        print("Training Iteration (Full Novice Rollouts): %d" % self.iteration)
        return np.mean([np.sum(p['true_rewards']) for p in novice_rollouts]), np.mean([np.sum(p['rewards']) for p in novice_rollouts])
