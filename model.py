import tensorflow as tf
import numpy as np
#from tensorflow.contrib.data import Dataset, TextLineDataset
import os
import chardet
#from sklearn.feature_extraction.text import CountVectorizer
import six
from tensorflow.python.training import device_setter
from tensorflow.core.framework import node_def_pb2
from tensorflow.python.framework import device as pydev

class attention_cnn(object):
    """docstring for attention_cnn"""
    def __init__(self, dataset, hparam):
        self.dataset = dataset
        self.vocab_len = len(dataset.vocab_map)
        self.hparam = hparam
        pass

    def get_embeding_var(self, hparam, word_emb_matrix):
        initializer = tf.constant_initializer(word_emb_matrix)
        with tf.variable_scope("embedding"):
            embeding_matrix = tf.get_variable(
                    name="embedding_matrix",
                    shape=[self.vocab_len, hparam.emb_size],
                    initializer=initializer,
                    dtype=tf.float32,
                    trainable = True)
        return embeding_matrix

    def gated_conv_network(self, input, shape, input_mask):

        kernel_conv = tf.get_variable(name='conv_kernel', 
                        shape=shape, 
                        initializer=tf.random_normal_initializer(seed=self.hparam.seed, dtype=tf.float32))
        kernel_gate = tf.get_variable(name='gate_kernel', 
                        shape=shape, 
                        initializer=tf.random_normal_initializer(seed=self.hparam.seed, dtype=tf.float32))
        bias_conv = tf.get_variable(name='bias_conv', 
                        shape=[self.hparam.emb_size], 
                        initializer=tf.random_normal_initializer(seed=self.hparam.seed, dtype=tf.float32))
        bias_gate = tf.get_variable(name='bias_gate', 
                        shape=[self.hparam.emb_size], 
                        initializer=tf.random_normal_initializer(seed=self.hparam.seed, dtype=tf.float32))

        conv_layer = tf.nn.conv1d(value = input, 
                                  filters = kernel_conv, 
                                  stride = 1,
                                  padding = 'SAME',
                                  name = 'CONV'
                                  )
        gate_layer = tf.nn.conv1d(value = input, 
                                  filters = kernel_gate, 
                                  stride = 1,
                                  padding = 'SAME',
                                  name = 'GATE'
                                  )
        tanh_out = tf.tanh(conv_layer + tf.expand_dims(tf.expand_dims(bias_conv, 0), 1))
        sigmod_out = tf.sigmoid(gate_layer + tf.expand_dims(tf.expand_dims(bias_gate, 0), 1))
        gated_conv = sigmod_out*input + (1.0-sigmod_out)*tanh_out

        #batch and dropout
        batchNorm_dropOut = self.batchnorm_dropout(gated_conv, [0,1], self.hparam.emb_size) * tf.expand_dims(input_mask, 2)

        return batchNorm_dropOut

    def batchnorm_dropout(self, input, axis, last_dim_size):
        mean, variance = tf.nn.moments(input, axis, keep_dims=True)
        beta = tf.Variable(tf.zeros(last_dim_size),
                            name='beta', 
                            trainable=True)
        gamma = tf.Variable(tf.zeros(last_dim_size),
                            name='gamma', 
                            trainable=True)
        batch_out = tf.nn.batch_normalization(input, mean, variance, beta, gamma, 1e-3)
        drop_out = tf.nn.dropout(batch_out,keep_prob=self.hparam.keep_prob)
        return drop_out


    def fullyconnect(self, input):
        drop_out = tf.nn.dropout(input,keep_prob=self.hparam.keep_prob)
        hiden_output = tf.layers.dense(drop_out, self.hparam.hidden_size, activation=tf.nn.relu)
        return hiden_output

    def emb_drop(self, input, sequence_mask):
        input_embding = tf.nn.embedding_lookup(self.word_embd_matrix, input)
        emb_dropout = tf.nn.dropout(input_embding, keep_prob=self.hparam.keep_prob) * tf.expand_dims(sequence_mask, -1)
        return emb_dropout

    def benificiay_conv_layer(self, input_emb, input_mask):
        benificiay_shape = [1, self.hparam.emb_size, self.hparam.emb_size]
        with tf.variable_scope("benificial"):
            benici_layer = self.gated_conv_network(input_emb, benificiay_shape, input_mask)

            benici_kernel_conv = tf.get_variable(name='benifi_conv_kernel', 
                                          shape=[3, self.hparam.emb_size, self.hparam.emb_size], 
                                          initializer=tf.random_normal_initializer(seed=self.hparam.seed, dtype=tf.float32))

            conv_layer = tf.nn.conv1d(value = tf.nn.dropout(benici_layer, keep_prob=self.hparam.keep_prob), 
                                      filters = benici_kernel_conv, 
                                      stride = 1,
                                      padding = 'SAME',
                                      name = 'benici_CONV'
                                      ) #tf.expand_dims(input_mask, 2)
            return conv_layer

    def get_attention_context(self, ngram_type, input_left_emb, orig_input_left_mask, input_right_emb, orig_input_right_mask):
        if ngram_type == 'unigram':
            attention_shape = [1, self.hparam.emb_size, self.hparam.emb_size]
        else:
            attention_shape = [3, self.hparam.emb_size, self.hparam.emb_size]


        with tf.variable_scope("left_gated_net"):
            left_attention_source = self.gated_conv_network(input_left_emb, attention_shape, orig_input_left_mask)
        with tf.variable_scope("right_gated_net"):
            right_attention_source = self.gated_conv_network(input_right_emb, attention_shape, orig_input_right_mask)


        with tf.variable_scope("l_r_attention_context"):
            # = [batch, left, right] * [batch,]
            attention_scores = tf.nn.softmax(tf.matmul(left_attention_source, tf.transpose(right_attention_source, perm=[0,2,1])))
            #[batch, left, emb] = [batch, left, right] * [batch, right, emb]
            attention_context_l = tf.matmul(attention_scores, right_attention_source) * tf.expand_dims(orig_input_left_mask, 2)
            attention_context_r = tf.matmul(tf.transpose(attention_scores, perm=[0,2,1]), left_attention_source) * \
                                                    tf.expand_dims(orig_input_right_mask, 2)


        return attention_context_l, attention_context_r


    def conv(self, input, input_mask, width):
        with tf.variable_scope("convolution"):
            kernel = tf.get_variable(name='conv_kernel', 
                            shape=[width, self.hparam.emb_size, self.hparam.emb_size], 
                            initializer=tf.random_normal_initializer(seed=self.hparam.seed, dtype=tf.float32))
            conv_layer = tf.nn.conv1d(value = input, 
                                      filters = kernel, 
                                      stride = 1,
                                      padding = 'SAME',
                                      name = 'CONV'
                                      ) * tf.expand_dims(input_mask, 2)
            return conv_layer


    def local_device_setter(self, num_devices=1,
                            ps_device_type='cpu',
                            worker_device='/cpu:0',
                            ps_ops=None,
                            ps_strategy=None):
      if ps_ops == None:
        ps_ops = ['Variable', 'VariableV2', 'VarHandleOp']

      if ps_strategy is None:
        ps_strategy = device_setter._RoundRobinStrategy(num_devices)
      if not six.callable(ps_strategy):
        raise TypeError("ps_strategy must be callable")

      def _local_device_chooser(op):
        current_device = pydev.DeviceSpec.from_string(op.device or "")

        node_def = op if isinstance(op, node_def_pb2.NodeDef) else op.node_def
        if node_def.op in ps_ops:
          ps_device_spec = pydev.DeviceSpec.from_string(
              '/{}:{}'.format(ps_device_type, ps_strategy(op)))
          ps_device_spec.merge_from(current_device)
          return ps_device_spec.to_string()
        else:
          worker_device_spec = pydev.DeviceSpec.from_string(worker_device or "")
          worker_device_spec.merge_from(current_device)
          return worker_device_spec.to_string()
      return _local_device_chooser


    def get_mode_fn(self, word_emb_matrix):
        def get_model(features, labels, mode, params):

            orig_input_left = features['orig_input_left']
            orig_input_left_mask = features['orig_input_left_mask']
            orig_input_right = features['orig_input_right']
            orig_input_right_mask = features['orig_input_right_mask']


            self.word_embd_matrix = self.get_embeding_var(params, word_emb_matrix)

            input_left_emb = self.emb_drop(orig_input_left, orig_input_left_mask)
            input_right_emb = self.emb_drop(orig_input_right, orig_input_right_mask)

            device_setter = self.local_device_setter(
                        worker_device='/gpu:0')
            with tf.device(device_setter):
                with tf.variable_scope("benificial_left"):
                    left_benificiay_layer1 = self.benificiay_conv_layer(input_left_emb, 
                                                                        orig_input_left_mask)
                with tf.variable_scope("benificial_right"):
                    right_benificiay_layer1 = self.benificiay_conv_layer(input_right_emb,
                                                                          orig_input_right_mask)


                with tf.variable_scope("unigram_attention"):
                    unigram_attn_l, unigram_attn_r = self.get_attention_context('unigram', input_left_emb, orig_input_left_mask, input_right_emb, orig_input_right_mask)
                with tf.variable_scope("trigram_attention"):
                    trigram_attn_l, trigram_attn_r = self.get_attention_context('trigram', input_left_emb, orig_input_left_mask, input_right_emb, orig_input_right_mask)


                with tf.variable_scope("attention_conv_l"):
                    with tf.variable_scope("unigram_attention"):
                        unigram_l_conv = self.conv(unigram_attn_l, orig_input_left_mask, 1)
                    with tf.variable_scope("trigram_attention"):
                        trigram_l_conv = self.conv(trigram_attn_l, orig_input_left_mask, 1)
                    with tf.variable_scope("benificial"):
                        trigram_l_beni_conv = self.conv(left_benificiay_layer1, orig_input_left_mask, 3)


                with tf.variable_scope("attention_conv_r"):
                    with tf.variable_scope("unigram_attention"):
                        unigram_r_conv = self.conv(unigram_attn_r, orig_input_right_mask, 1)
                    with tf.variable_scope("trigram_attention"):
                        trigram_r_conv = self.conv(trigram_attn_r, orig_input_right_mask, 1)
                    with tf.variable_scope("benificial"):
                        trigram_r_beni_conv = self.conv(right_benificiay_layer1, orig_input_right_mask, 3)


                with tf.variable_scope("beni_uni_tri_l"):
                    bias_l = tf.get_variable(name='bias_l', 
                                shape=(params.emb_size,), 
                                initializer=tf.random_normal_initializer(seed=params.seed, dtype=tf.float32))
                    left_out = tf.tanh((unigram_l_conv+trigram_l_conv+trigram_l_beni_conv + bias_l[tf.newaxis, tf.newaxis, :]) *\
                                        orig_input_left_mask[:, :, tf.newaxis])

                with tf.variable_scope("beni_uni_tri_r"):
                    bias_r = tf.get_variable(name='bias_r', 
                                    shape=(params.emb_size,), 
                                    initializer=tf.random_normal_initializer(seed=params.seed, dtype=tf.float32))
                    right_out = tf.tanh((unigram_r_conv+trigram_r_conv+trigram_r_beni_conv + bias_r[tf.newaxis, tf.newaxis, :]) *\
                                        orig_input_right_mask[:, :, tf.newaxis])


                with tf.name_scope("projection"):
                    left_max_pool = tf.reduce_max(left_out, axis=1)
                    right_max_pool = tf.reduce_max(right_out, axis=1)

                    hidden_input = tf.concat([left_max_pool, right_max_pool], -1)
                    hiden_output = self.fullyconnect(hidden_input)


                with tf.name_scope("concat_hidden_input"):
                    final_input = tf.concat([hidden_input, hiden_output], -1)
                    #labels:[batch, seq], loss is a scalar
                with tf.name_scope("loss_layer"):
                    loss, logits = self.get_loss(final_input, labels, params)
                    self.loss = self.l2_norm(loss)


                '''
                staged_lr = [0.05, 0.01, 0.005, 0.001]
                boundaries = [
                  params.train_sum/params.batch_size * x
                  for x in np.array([20, 40, 80], dtype=np.int64)
                ]

                learning_rate = tf.train.piecewise_constant(tf.train.get_global_step(),
                                                      boundaries, staged_lr)
                '''
                #self.descent_lr()
                self.gradient_clap_and_train(params.grad_clip)

            with tf.name_scope("predict"):
                #(batch,0)
                predict = tf.argmax(logits, axis=1)

            predictions = {
              'classes':
                  predict[:, tf.newaxis],#add a new dimension to [batch,1]
              'probabilities':
                  tf.nn.softmax(logits),
              'logits': logits
            }

            with tf.name_scope("accuracy"):
                #labels must have the same shape with predict. and there can be any shape
                accuracy = tf.metrics.accuracy(labels=tf.argmax(labels, axis=1),
                                               predictions=predict,
                                               name='acc_op')
                tf.summary.scalar(name='accuracy_train', tensor=accuracy[0])
            '''
            tensors_to_log = {'learning_rate': self.lr, 'loss': self.loss}
            logging_hook = tf.train.LoggingTensorHook(tensors=tensors_to_log, every_n_iter=1)
            train_hooks = [logging_hook]
            '''
            
            metrics = {'accuracy': accuracy}
            return tf.estimator.EstimatorSpec(
                mode=mode,
                eval_metric_ops=metrics,
                predictions=predictions,
                loss=self.loss,#loss must be a scalar
                train_op=self.train_op,
                #training_hooks=train_hooks
                )

        return get_model

    def l2_norm(self, loss):
        with tf.name_scope("add_l2_loss"):
            var = tf.trainable_variables()
            regularizer = tf.contrib.layers.l2_regularizer(scale = self.hparam.l2_c)
            l2_loss = tf.contrib.layers.apply_regularization(regularizer, var)
            tf.summary.scalar(name='l2_norm', tensor=l2_loss)
            tf.summary.scalar(name='befor_norm', tensor=loss)
            return loss + l2_loss

    def descent_lr(self):
        starter_learning_rate = 0.04
        self.lr = tf.train.exponential_decay(starter_learning_rate, tf.train.get_global_step(),
                                           10000, 0.5, staircase=True)
        tf.summary.scalar(name='learning_rate', tensor=self.lr)

    def ascent_lr(self):
        self.lr = tf.minimum(self.hparam.lr, 0.0001 / tf.log(9999.) * tf.log(tf.cast(tf.train.get_global_step(), tf.float32) + 1))
        tf.summary.scalar(name='learning_rate', tensor=self.lr)

    def gradient_clap_and_train(self, grad_clip):
        with tf.name_scope("gradiet_calc"):
            self.ascent_lr()
            if self.hparam.opt == 'sgd':
                self.opt = tf.train.GradientDescentOptimizer(0.02)
            else:
                self.opt = tf.train.AdamOptimizer(learning_rate = self.lr, beta1 = 0.8, beta2 = 0.999, epsilon = 1e-7)
            grads = self.opt.compute_gradients(self.loss)

        with tf.name_scope("gradiet_clap"):
            gradients, variables = zip(*grads)
            capped_grads, _ = tf.clip_by_global_norm(
                gradients, grad_clip)
            self.train_op = self.opt.apply_gradients(
                zip(capped_grads, variables), global_step=tf.train.get_global_step())

    def get_loss(self, input, labels, hparam):
        with tf.name_scope("norm_loss_input"):
            drop_out = self.batchnorm_dropout(input, [0], hparam.emb_size*2+hparam.hidden_size)
        with tf.variable_scope("raw_loss"):
            w = tf.get_variable(name='w', 
                            shape=(hparam.emb_size*2+hparam.hidden_size, 3), 
                            initializer=tf.random_normal_initializer(seed=hparam.seed, dtype=tf.float32))
            bias_out = tf.get_variable(name='b_out', 
                            shape=(3,), 
                            initializer=tf.random_normal_initializer(seed=hparam.seed, dtype=tf.float32))   
            logits = tf.nn.xw_plus_b(drop_out, w, bias_out)
            #loss:(batch,)
            '''
            tf.nn.softmax_cross_entropy_with_logits : return (batch,)
            logits have the same shape with labels: [batch, seq]
            '''
            cross_entropy_loss = tf.nn.softmax_cross_entropy_with_logits(logits=logits, labels=labels)
            loss = tf.reduce_mean(cross_entropy_loss)
            return loss, logits


