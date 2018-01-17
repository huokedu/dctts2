# -*- coding: utf-8 -*-
'''
June 2017 by kyubyong park. 
kbpark.linguist@gmail.com.
https://www.github.com/kyubyong/transformer
'''
from __future__ import print_function
import tensorflow as tf
from matplotlib import pyplot as plt
from hyperparams import Hyperparams as hp
from modules import *
import os
import time
import numpy as np
import re
import audio

def load_vocab():
	#characters = "PSEاإأآبتثجحخدذرزسشصضطظعغفقكلمنهويىؤءةئ ًٌٍَُِّْ،." # Arabic character set
	characters = "PE abcdefghijklmnopqrstuvwxyz'.,?"  # P: Padding E: End of Sentence
	
	char2idx = {char: idx for idx, char in enumerate(characters)}
	idx2char = {idx: char for idx, char in enumerate(characters)}
	return char2idx, idx2char
	
def clean(text):
	text=text.lower()
	re_list = r"[^ abcdefghijklmnopqrstuvwxyz'.,?]" # E: Empty. ignore G
	_text = re.sub(re_list, "", text)
	return(_text)
		
	
def get_data():
	def mypyfunc(text):
		text = text.decode("utf-8")
		items = text.split("|")
		char2idx,_=load_vocab()
		text = items[2].lower()
		text = clean(text)
		source = [char2idx[c] for c in text+'E']
		dest = items[0]
		mels = np.load(os.path.join(hp.data_dir, "mels", dest + ".npy"))
		mels = mels[::4,:]
		mags = np.load(os.path.join(hp.data_dir, "mags", dest + ".npy"))
		return np.array(source, dtype=np.int32),mels,mags,len(text),mels.shape[0]
	def _pad(text,mel,mag,textlen,mellen):
		text = tf.pad(text, ((0, hp.maxlen),))[:hp.maxlen] # (Tx,)
		mel = tf.pad(mel, ((0, hp.Tyr), (0, 0)))[:hp.Tyr] # (Tyr, n_mels)
#		textlen = tf.cast(textlen,tf.int32)
#		mellen = tf.cast(mellen,tf.int32)
		mag = tf.pad(mag, ((0, hp.Ty), (0, 0)))[:hp.Ty] # (Ty, 1+n_fft/2)
		return text,mel,mag,textlen,mellen
	#filenames = tf.gfile.Glob("data/*.txt")
	dataset = tf.data.TextLineDataset(tf.convert_to_tensor(hp.metafile))
	dataset = dataset.map(lambda text: tuple(tf.py_func(mypyfunc, [text], [tf.int32, tf.float32, tf.float32, tf.int64,tf.int64])))
	dataset = dataset.map(_pad)
	#dataset = dataset.filter(lambda x,y: tf.less_equal(tf.size(y),hp.maxlen))
	dataset = dataset.repeat()
	dataset = dataset.batch(hp.batch_size)
	iterator = dataset.make_one_shot_iterator()
	next_element = iterator.get_next()
	return(next_element)


def w2(n,t):
	return np.exp(-((n/(hp.maxlen-1) - t/(hp.Tyr-1))**2) / (2 * 0.02**2))
	
def w_fun(n, t):
	return 1 - np.exp(-((n/(hp.maxlen-1) - t/(hp.Tyr-1))**2) / (2 * hp.g**2))
def guide_fn(x):
	prva=-1
	for i in range(x.shape[1]):
		
		pos = np.argmax(x[:,i])
		val = x[pos,i]
		if (pos<prva-1) or (pos>prva+3):
			x[:,i]=np.zeros(x.shape[0],dtype='f')
			pp = min(x.shape[0]-1,prva+1)
			x[pp,i]=1
			#print("%d-Corrected from %d to %d - prva %d"%(i,pos,pp,prva))
		else:
			x[:,i]=np.zeros(x.shape[0],dtype='f')
			x[pos,i]=1
			pass
			#print("%d-Was ok %d - prva %d"%(i,pos,prva))
		prva=np.argmax(x[:,i])
	return x


def guide_atten(inputs): # 180,XX
	return tf.py_func(guide_fn,[inputs],tf.float32)

class Graph():
	def __init__(self, is_training=True):
		self.graph = tf.Graph()
		with self.graph.as_default():
			if is_training:
				self.text, self.mel, self.mag, self.textlen, self.mellen= get_data() # (N, T), (N,Ty,nmels), (N,Ty,nffts)
				#self.text = tf.reshape(self.text,shape=[-1,hp.maxlen])
				self.mel = tf.reshape(self.mel,shape=[-1,hp.Tyr,hp.n_mels])
				#self.mag = tf.reshape(self.mag,shape=[-1,hp.Ty,1+hp.n_fft//2])
				w = np.fromfunction(w_fun, (hp.maxlen, hp.Tyr), dtype='f')
				w = np.expand_dims(w,0)
				w = np.repeat(w,hp.batch_size,0)
				self.A_guide = tf.convert_to_tensor(w) # B,180,870
			
#				self.y = tf.reshape(self.y,shape=[-1,hp.Ty,])
			else: # inference
				self.text = tf.placeholder(tf.int32, shape=(None, hp.maxlen))
#				self.mel = tf.placeholder(tf.float32, shape=(None,hp.Tyr,hp.n_mels))
				self.mel = tf.placeholder(tf.float32, shape=(None,None,hp.n_mels))
				w = np.fromfunction(w2, (hp.maxlen, hp.Tyr), dtype='f')
				w = np.expand_dims(w,0)
				#w = np.repeat(w,2,0)
				self.A_guide = tf.convert_to_tensor(w)
			
				#self.y = tf.placeholder(tf.int32, shape=(None, hp.maxlen))

			# define decoder inputs
			if is_training:
				self.decoder_inputs = tf.concat((tf.zeros_like(self.mel[:, :1,:]), self.mel[:, :-1,:]), 1) # shift mels to right
			else:
				#self.decoder_inputs = tf.concat((tf.zeros_like(self.mel[:, :1,:]), self.mel[:, :-1,:]), 1) # shift mels to right
				self.decoder_inputs=self.mel
			char2idx, idx2char = load_vocab()
			with tf.variable_scope("Text2Mel"):
				with tf.variable_scope("TextEnc"):
					self.emb=embedding(self.text,
										vocab_size=len(char2idx), 
										num_units=hp.e,
										scale = False,
										scope="embedding") #in (N,T) out (N,T,e) (32,180,128)
					self.textenc=Conv1D(self.emb,hp.d*2,1,1,causal=False,is_training=is_training,activation=tf.nn.relu,scope='c1d-1')
					self.textenc=Conv1D(self.textenc,hp.d*2,1,1,causal=False,is_training=is_training,scope='c1d-2')
					for i in range(2):
						self.textenc=HConv1D(self.textenc,hp.d*2,3,1,causal=False,is_training=is_training,scope='hc1d-1-%d'%i)
						self.textenc=HConv1D(self.textenc,hp.d*2,3,3,causal=False,is_training=is_training,scope='hc1d-2-%d'%i)
						self.textenc=HConv1D(self.textenc,hp.d*2,3,9,causal=False,is_training=is_training,scope='hc1d-3-%d'%i)
						self.textenc=HConv1D(self.textenc,hp.d*2,3,27,causal=False,is_training=is_training,scope='hc1d-4-%d'%i)
					for i in range(2):
						self.textenc=HConv1D(self.textenc,hp.d*2,3,1,causal=False,is_training=is_training,scope='hc1d-11-%d'%i)
					for i in range(2):
						self.textenc=HConv1D(self.textenc,hp.d*2,1,1,causal=False,is_training=is_training,scope='hc1d-12-%d'%i) #(N,T,2*d) (32,180,512)

					
					self.K,self.V = tf.split(self.textenc,num_or_size_splits=2,axis=2)	#k=(B,N,d) v=(B,N,d)
				with tf.variable_scope("AudioEnc"):
					self.audioenc = Conv1D(self.decoder_inputs,hp.d,1,1,is_training=is_training,activation=tf.nn.relu,scope='c1d-1') # from (B,Ty,80) -> (B,Ty,d)
					self.audioenc = Conv1D(self.audioenc,hp.d,1,1,is_training=is_training,activation=tf.nn.relu,scope='c1d-2')
					self.audioenc = Conv1D(self.audioenc,hp.d,1,1,is_training=is_training,scope='c1d-3')
					for i in range(2):
						self.audioenc=HConv1D(self.audioenc,hp.d,3,1,is_training=is_training,scope='hc1d-1-%d'%i)
						self.audioenc=HConv1D(self.audioenc,hp.d,3,3,is_training=is_training,scope='hc1d-2-%d'%i)
						self.audioenc=HConv1D(self.audioenc,hp.d,3,9,is_training=is_training,scope='hc1d-3-%d'%i)
						self.audioenc=HConv1D(self.audioenc,hp.d,3,27,is_training=is_training,scope='hc1d-4-%d'%i)
					for i in range(2):
						self.audioenc=HConv1D(self.audioenc,hp.d,3,3,is_training=is_training,scope='hc1d-11-%d'%i)
					self.Q = self.audioenc										# (B,Ty,d)

				if is_training and False:
					self.seqTm = tf.expand_dims(tf.sequence_mask(self.textlen,hp.maxlen),2) # B,180,1
					self.seqTm = tf.tile(self.seqTm,[1,1,hp.d])
					self.seqMm = tf.expand_dims(tf.sequence_mask(self.mellen,hp.Tyr),2) #B,870,1
					self.seqMm = tf.tile(self.seqMm,[1,1,hp.d])
					self.Kc = tf.where(self.seqTm,self.K,tf.zeros_like(self.K))
					self.Vc = tf.where(self.seqTm,self.V,tf.zeros_like(self.V))
					self.Qc = tf.where(self.seqMm,self.Q,tf.zeros_like(self.Q))
				self.KT = tf.transpose(self.K,perm=[0,2,1]) # B,d,180
				self.VT = tf.transpose(self.V,perm=[0,2,1]) # B,d,180
				self.QT = tf.transpose(self.Q,perm=[0,2,1]) # B,d,870
					
				self.A = tf.matmul(self.K,self.QT)	  # (B,180,d) * (B,d,870) = (B,180,870)
				self.A *= tf.sqrt(1/tf.to_float(hp.d))
				self.A = tf.nn.softmax(self.A,dim=1) #B,180,870
				#self.A *=1000
				if not is_training:
					self.A = tf.map_fn(guide_atten,self.A,parallel_iterations=1)
					pass
				self.AT = tf.transpose(self.A,perm=[0,2,1]) # (B,870,180)
				#self.AT = tf.nn.softmax(self.AT)
				self.R = tf.matmul(self.VT,self.A)			# B,d,180 * B,180,870 -> B,d,870
				self.RT = tf.transpose(self.R,perm=[0,2,1]) # B,870,d
				self.Rhat = tf.concat((self.RT,self.Q),2)		# (B,Ty,d),(B,Ty,d) --> (B,Ty,2d)
				#self.Rhat = tf.transpose(self.Rha,perm=[0,2,1])
				with tf.variable_scope("AudioDec"):
					self.audiodec = Conv1D(self.Rhat,hp.d,1,1,is_training=is_training,scope='c1d-1')
					self.audiodec=HConv1D(self.audiodec,hp.d,3,1,is_training=is_training,scope='hc1d-1')
					self.audiodec=HConv1D(self.audiodec,hp.d,3,3,is_training=is_training,scope='hc1d-2')
					self.audiodec=HConv1D(self.audiodec,hp.d,3,9,is_training=is_training,scope='hc1d-3')
					self.audiodec=HConv1D(self.audiodec,hp.d,3,27,is_training=is_training,scope='hc1d-4')
					for i in range(2):
						self.audiodec=HConv1D(self.audiodec,hp.d,3,1,is_training=is_training,scope='hc1d-5-%d'%i)
					for i in range(3):
						self.audiodec=Conv1D(self.audiodec,hp.d,1,1,dropout=0,is_training=is_training,scope='c1d-2-%d'%i,activation=tf.nn.relu)
					self.mel_logits = Conv1D(self.audiodec,hp.n_mels,1,1,dropout=0,is_training=is_training,scope='c1d-3') # (B,Tyr,nmels)
					self.mel_output = tf.nn.sigmoid(self.mel_logits)														#(B,Tyr,nmels)
			
			with tf.variable_scope("SSRN"):
				self.ssrn = Conv1D(self.mel,hp.c,1,1,causal=False,is_training=is_training,scope='c1d-1')
				self.ssrn = HConv1D(self.ssrn,hp.c,3,1,causal=False,is_training=is_training,scope='hc1d-1')
				self.ssrn = HConv1D(self.ssrn,hp.c,3,3,causal=False,is_training=is_training,scope='hc1d-2')
				for i in range(2):
					self.ssrn = Deconv1D(self.ssrn,hp.c,2,1,scope='deconv-%d'%i)
					self.ssrn = HConv1D(self.ssrn,hp.c,3,1,causal=False,is_training=is_training,scope='hc1d-31-%d'%i)
					self.ssrn = HConv1D(self.ssrn,hp.c,3,3,causal=False,is_training=is_training,scope='hc1d-32-%d'%i)
				self.ssrn = Conv1D(self.ssrn,hp.c*2,1,1,causal=False,is_training=is_training,scope='c1d-2')
				for i in range(2):
					self.ssrn=HConv1D(self.ssrn,hp.c*2,3,1,causal=False,is_training=is_training,scope='hc1d-4-%d'%i)
				self.ssrn = Conv1D(self.ssrn,hp.fd,1,1,causal=False,is_training=is_training,scope='c1d-3')
				for i in range(2):
					self.ssrn=Conv1D(self.ssrn,hp.fd,1,1,causal=False,is_training=is_training,activation=tf.nn.relu,scope='c1d-4-%d'%i)
				self.mag_logits = Conv1D(self.ssrn,hp.fd,1,1,causal=False,is_training=is_training,scope='c1d-5')
				self.mag_output = tf.nn.sigmoid(self.mag_logits)
			if is_training:	 
				# Loss
				self.global_step = tf.Variable(0, name='global_step', trainable=False)
				self.learning_rate = _learning_rate_decay(self.global_step)
				#self.istarget = tf.to_float(tf.not_equal(self.text, 0)) # (batch,180) (1,1,1,1,0,0,0,0,0,0,0,0,0,0)
				
				
				self.mel_l1_loss = tf.reduce_mean(tf.abs(self.mel-self.mel_output))
				self.mel_bin_div = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=self.mel_logits,labels=self.mel))
				self.A_loss = tf.reduce_mean(self.A_guide*self.A)
				self.mag_l1_loss = tf.reduce_mean(tf.abs(self.mag-self.mag_output))
				self.mag_bin_div = tf.reduce_mean(tf.nn.sigmoid_cross_entropy_with_logits(logits=self.mag_logits,labels=self.mag))
				
#				self.l1_loss = tf.abs(self.mel-self.mel_output) * tf.to_float(tf.not_equal(self.mel, 0.))
#				self.bin_div = tf.nn.sigmoid_cross_entropy_with_logits(logits=self.mel_logits,labels=self.mel) * tf.to_float(tf.not_equal(self.mel, 0.))
#				self.l1_loss = tf.reduce_mean(self.l1_loss)
#				self.bin_div = tf.reduce_mean(self.bin_div)
#				self.A_loss = tf.reduce_mean(self.A_guide*self.A)
				
				
				
				self.loss_mels = self.mel_l1_loss + self.mel_bin_div + self.A_loss
				self.loss_mags = self.mag_l1_loss + self.mag_bin_div
				self.loss_all = self.loss_mels + self.loss_mags
				self.optimizer = tf.train.AdamOptimizer(learning_rate=self.learning_rate, beta1=hp.b1, beta2=hp.b2, epsilon=hp.eps)
#				self.gvs = self.optimizer.compute_gradients(self.loss_mels) 
#				self.clipped = []
#				for grad, var in self.gvs:
#					if grad is not None:
#						grad = tf.clip_by_norm(grad, hp.max_grad_norm)
#						
#					self.clipped.append((grad, var))
#				self.train_op = self.optimizer.apply_gradients(self.clipped, global_step=self.global_step)
				self.train_mel = self.optimizer.minimize(self.loss_mels,global_step=self.global_step)
				self.train_mag = self.optimizer.minimize(self.loss_mags,global_step=self.global_step)
				self.train_all = self.optimizer.minimize(self.loss_all,global_step=self.global_step)
				#self.train_op = self.optimizer.apply_gradients(self.clipped, global_step=self.global_step)
				
				#self.train_op = self.optimizer.minimize(self.loss_mels, global_step=self.global_step)
				tf.summary.scalar('Total_Loss', self.loss_all)
				tf.summary.scalar('loss_mels', self.loss_mels)
				tf.summary.scalar('loss_mel_l1', self.mel_l1_loss)
				tf.summary.scalar('loss_mel_binary', self.mel_bin_div)
				tf.summary.scalar('loss_Attention', self.A_loss)
				tf.summary.scalar('loss_mags', self.loss_mags)
				tf.summary.scalar('loss_mag_binary', self.mag_bin_div)
				tf.summary.scalar('loss_mag_l1', self.mag_l1_loss)
			self.merged = tf.summary.merge_all()

def show(mel1,mel2,name):
	plt.figure(figsize=(8,4))
	plt.subplot(2,1,1)
	plt.imshow(np.transpose(mel1),interpolation='nearest',  cmap=plt.cm.afmhot, origin='lower')
	plt.title("Generated")
	plt.colorbar()
	plt.subplot(2,1,2)
	plt.imshow(np.transpose(mel2),interpolation='nearest',  cmap=plt.cm.afmhot, origin='lower')
	plt.title("Original")
	plt.colorbar()
	plt.savefig(name)
	plt.cla()
	plt.close('all')

			
def showmels(mel,msg,file):
	fig, ax = plt.subplots(nrows=1,ncols=1, figsize=(8,4))
	cax = ax.matshow(mel, interpolation='nearest',  cmap=plt.cm.afmhot, origin='lower')
	fig.colorbar(cax)
	plt.title(msg+str(len(msg)))
	plt.savefig(file,format='png')
	plt.cla()
	plt.close('all')

def _learning_rate_decay(global_step):
  # Noam scheme from tensor2tensor:
  step = tf.cast(global_step + 1, dtype=tf.float32)
  return hp.lr
  #return hp.c**-0.5 * tf.minimum(step * hp.warmup_steps**-1.5, step**-0.5)


def tdecode(text):
	char2idx,idx2char=load_vocab()
	return("".join(idx2char[i] for i in text).split('P')[0])

				
if __name__ == '__main__':	
	g = Graph(); print("Training Graph loaded")
	sv = tf.train.Supervisor(graph=g.graph, 
							 logdir=hp.logdir,)
							 #save_model_secs=0)
	train = 3 # 1=mels. 2=mags 3=all
	with sv.managed_session() as sess:
		while not sv.should_stop():
			if train == 1:
				gs,l_m,l_m_l1,l_m_b,l_A,t_i,m_i,a,m_o,ops = sess.run([g.global_step,
					g.loss_mels,g.mel_l1_loss,g.mel_bin_div,g.A_loss,g.text,g.mel,g.A,g.mel_output,g.train_mel])
				message = "Step %-7d : loss=%.05f,l1=%.05f,bin=%.05f,A_loss=%.05f" % (gs,l_m,l_m_l1,l_m_b,l_A)
				print(message)
				if gs % hp.logevery == 0:
					show(m_o[0],m_i[0],"0.png")
					show(m_o[1],m_i[1],"1.png")
					showmels(a[0],tdecode(t_i[0]),"a0.png")
					showmels(a[1],tdecode(t_i[1]),"a1.png")
				
			elif train == 2:
				gs,l_M,l_M_l1,l_M_b,M_i,M_o,ops = sess.run([g.global_step,
					g.loss_mags,g.mag_l1_loss,g.mag_bin_div,g.mag,g.mag_output,g.train_mag])
				message = "Step %-7d : loss=%.05f,l1=%.05f,bin=%.05f" % (gs,l_M,l_M_l1,l_M_b)
				print(message)
				if gs % hp.logevery == 0:
					show(M_o[0],M_i[0],"0.png")
					show(M_o[1],M_i[1],"1.png")
					#showmels(a[0],tdecode(t_i[0]),"a0.png")
					#showmels(a[1],tdecode(t_i[1]),"a1.png")
			elif train == 3:
				gs,l_all,l_m_l1,l_m_b,l_A,l_M_l1,l_M_b,ops = sess.run([g.global_step,
					g.loss_all,g.mel_l1_loss,g.mel_bin_div,g.A_loss,g.mag_l1_loss,g.mag_bin_div,g.train_all])
				message = "Step %d : l=%.05f (ml1=%.05f,mb=%.05f,a=%.05f),(Ml1=%.05f,Mb=%.05f)" % (gs,l_all,l_m_l1,l_m_b,l_A,l_M_l1,l_M_b)
				print(message)
				if gs % hp.logevery == 0:
					gs,l_all,l_m_l1,l_m_b,l_A,l_M_l1,l_M_b,m_o,m_i,a,M_o,M_i,t_i,ops = sess.run([g.global_step,
						g.loss_all,g.mel_l1_loss,g.mel_bin_div,g.A_loss,g.mag_l1_loss,g.mag_bin_div,
						g.mel_output,g.mel, g.A, g.mag_output, g.mag,g.text,g.train_all])
					message = "Step %d : l=%.05f (ml1=%.05f,mb=%.05f,a=%.05f),(Ml1=%.05f,Mb=%.05f)" % (gs,l_all,l_m_l1,l_m_b,l_A,l_M_l1,l_M_b)
					#message = "Step %-7d : loss=%.05f (m_l1=%.05f,m_bin=%.05f,A_loss=%.05f),(M_l1=%.05f,M_bin=%.05f)" % (gs,l_all,l_m_l1,l_m_b,l_A,l_M_l1,l_M_b)
					print(message)
					audio.save_spec(M_o[0].T,"out0.wav")
					audio.save_spec(M_o[1].T,"out1.wav")
					show(M_o[0],M_i[0],"mag0.png")		
					show(M_o[1],M_i[1],"mag1.png")		
					show(m_o[0],m_i[0],"mel0.png")		
					show(m_o[1],m_i[1],"mel1.png")
					showmels(a[0],tdecode(t_i[0]),"a0.png")
					showmels(a[1],tdecode(t_i[1]),"a1.png")
				pass
				

	print("Done")	 
	

