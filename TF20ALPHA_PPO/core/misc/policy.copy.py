import tensorflow as tf
import numpy as np
from core.models import pi_model, v_model, pi_gaussian_model
import tensorflow.keras.losses as kls
from utils.logger import log


class Policy_PPO():

    def __init__(self,
                 lr_v=0.001,
                 lr_pi=0.001,
                 hidden_sizes_pi=(64, 64),
                 hidden_sizes_v=(64, 64),
                 train_pi_iters=80,
                 train_v_iters=80,
                 clip_ratio=0.2,
                 target_kl=0.01,
                 ent_coef = 0,
                 num_actions=None, 
                 act_space_type='discrete'):

        self.lr_v = lr_v
        self.lr_pi = lr_pi
        self.train_pi_iters = train_pi_iters
        self.train_v_iters = train_v_iters
        self.clip_ratio = clip_ratio
        self.target_kl = target_kl
        self.ent_coef = ent_coef
        self.num_actions = num_actions

        if act_space_type == 'discrete':
            self.pi = pi_model(hidden_sizes_pi, num_actions)
        elif act_space_type == 'continuous':
            self.pi = pi_gaussian_model(hidden_sizes= hidden_sizes_pi, num_outputs= num_actions)

        self.v = v_model(hidden_sizes_v)

        self.optimizer_pi = tf.keras.optimizers.Adam(lr=lr_pi)
        self.optimizer_v = tf.keras.optimizers.Adam(lr=lr_v)

    
    def update(self, observations, actions, advs, returns, logp_t):

        if act_space_type == 'discrete':
            # Policy Update Cycle for iter steps
            for i in range(self.train_pi_iters):
                loss_pi, loss_entropy, approx_ent, kl = self.train_pi_one_step(self.pi, self.optimizer_pi, observations, actions, advs, logp_t)
                if kl > 1.5 * self.target_kl:
                    log("Early stopping at step %d due to reaching max kl." %i)
                    break
                    
        elif act_space_type == 'continuous':
            for i in range(self.train_pi_iters):
                loss_pi, loss_entropy, approx_ent, kl = self.train_pi_continuous_one_step(self.pi, self.optimizer_pi, observations, actions, advs, logp_t)
                if kl > 1.5 * self.target_kl:
                    log("Early stopping at step %d due to reaching max kl." %i)
                    break

        # Value Update Cycle for iter steps
        for _ in range(self.train_v_iters):
            loss_v = self.train_v_one_step(self.v, self.optimizer_v, observations, returns)
            
        # Return Metrics
        return loss_pi.numpy().mean(), loss_entropy.numpy().mean(), approx_ent.numpy().mean(), kl.numpy().mean(), loss_v.numpy().mean()
        
        
    def save(self):

        log('Saving Model ...')
        self.pi.save_weights('./tmp/ckpts/pi', save_format='tf')
        self.v.save_weights('./tmp/ckpts/v', save_format='tf')

    def load(self):
        
        log('Loading Model ...')
        self.pi.load_weights('./tmp/ckpts/pi')
        self.v.load_weights('./tmp/ckpts/v')


    def _value_loss(self, returns, value):

        # Mean Squared Error
        loss = tf.reduce_mean((returns - value)**2)
        return loss # return kls.mean_squared_error(returns, value)


    def _pi_loss(self, logits, logp_old, act, adv):

        # PPO Objective 
        logp_all = tf.nn.log_softmax(logits)
        logp = tf.reduce_sum( tf.one_hot(act, self.num_actions) * logp_all, axis=1)
        ratio = tf.exp(logp-logp_old)
        min_adv = tf.where(adv > 0, (1+ self.clip_ratio) * adv, (1-self.clip_ratio) * adv)

        # Policy Gradient Loss
        pi_loss = -tf.reduce_mean(tf.minimum(ratio * adv, min_adv))

        # Entropy loss can be calculated via CE over itself ??
        # entropy_loss_2 = tf.reduce_mean(kls.categorical_crossentropy(logits, logits, from_logits=True))
        entropy_loss = tf.reduce_mean(self.entropy(logits))

        # Total Loss
        pi_loss -= self.ent_coef * entropy_loss

        # Approximated  Kullback Leibler Divergence from OLD and NEW Policy
        approx_kl = tf.reduce_mean(logp_old-logp)
        approx_ent = tf.reduce_mean(-logp) 

        return pi_loss, entropy_loss, approx_ent, approx_kl

    
    def _pi_loss_continuous(self, mu, logp_old, act, adv):

        log_std = tf.Variable(name= 'log_std', initial_value= -0.5 * np.ones(self.num_actions, dtype=np.float32))
        logp = self.gaussian_likelihood(act, mu, log_std)

        ratio = tf.exp(logp-logp_old)
        min_adv = tf.where(adv > 0, (1+ self.clip_ratio) * adv, (1-self.clip_ratio) * adv)

        # Policy Gradient Loss
        pi_loss = -tf.reduce_mean(tf.minimum(ratio * adv, min_adv))

        # Entropy term
        entropy = tf.reduce_sum(log_std + 0.5 * np.log(2.0 * np.pi * np.e), axis=-1)
        entropy_loss = tf.reduce_mean(entropy)

        # Total Loss
        pi_loss -= self.ent_coef * entropy_loss

        # Approximated  Kullback Leibler Divergence from OLD and NEW Policy
        approx_kl = tf.reduce_mean(logp_old-logp)
        approx_ent = tf.reduce_mean(-logp) 

        return pi_loss, entropy_loss, approx_ent, approx_kl

    
    #@tf.function
    def train_pi_continuous_one_step(self, model, optimizer, obs, act, adv, logp_old):

        with tf.GradientTape() as tape:

            mu = model(obs)
            pi_loss, entropy_loss, approx_ent, approx_kl  = self._pi_loss_continuous(mu, logp_old, act, adv)
            
        grads = tape.gradient(pi_loss, model.trainable_variables)
        optimizer.apply_gradients(zip(grads, model.trainable_variables))

        return pi_loss, entropy_loss, approx_ent, approx_kl


    
    @tf.function
    def train_pi_one_step(self, model, optimizer, obs, act, adv, logp_old):

        with tf.GradientTape() as tape:

            logits = model(obs)
            pi_loss, entropy_loss, approx_ent, approx_kl  = self._pi_loss(logits, logp_old, act, adv)
            
        grads = tape.gradient(pi_loss, model.trainable_variables)
        optimizer.apply_gradients(zip(grads, model.trainable_variables))

        return pi_loss, entropy_loss, approx_ent, approx_kl


    @tf.function()
    def train_v_one_step(self, model, optimizer_v, obs, returns):

        with tf.GradientTape() as tape:

            values = model(obs)
            v_loss = self._value_loss(returns, values)

        grads = tape.gradient(v_loss, model.trainable_variables)
        optimizer_v.apply_gradients(zip(grads, model.trainable_variables))

        return v_loss


    def entropy(self, logits):

        '''
        Entropy term for more randomness which means more exploration in ppo -> 
        
        Due to machine precission error -> 
        entropy = - tf.reduce_sum (logp_all * tf.log(logp_all) + 1E-12, axis=-1, keepdims=True) 
        cannot be calculated this way
        '''
        
        a0 = logits - tf.reduce_max(logits, axis=-1, keepdims=True)
        exp_a0 = tf.exp(a0)
        z0 = tf.reduce_sum(exp_a0, axis=-1, keepdims=True)
        p0 = exp_a0 / z0
        entropy = tf.reduce_sum(p0 * (tf.math.log(z0) - a0), axis=-1)

        return entropy

