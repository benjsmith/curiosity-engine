# Dropout: A Simple Way to Prevent Neural Networks from Overfitting (Srivastava et al., 2014)

Authors: Nitish Srivastava, Geoffrey Hinton, Alex Krizhevsky, Ilya Sutskever, Ruslan Salakhutdinov.
Year: 2014
Journal: Journal of Machine Learning Research 15 (2014) 1929-1958

## Abstract

Deep neural nets with a large number of parameters are very powerful machine learning systems. However, overfitting is a serious problem in such networks. Large networks are also slow to use, making it difficult to deal with overfitting by combining the predictions of many different large neural nets at test time. Dropout is a technique for addressing this problem. The key idea is to randomly drop units (along with their connections) from the neural network during training. This prevents units from co-adapting too much. During training, dropout samples from an exponential number of different "thinned" networks. At test time, it is easy to approximate the effect of averaging the predictions of all these thinned networks by simply using a single un-thinned network that has smaller weights. This significantly reduces overfitting and gives major improvements over other regularization methods. We show that dropout improves the performance of neural networks on supervised learning tasks in vision, speech recognition, document classification, and computational biology, obtaining state-of-the-art results on many benchmark data sets.

## Introduction

Deep neural networks contain multiple non-linear hidden layers and this makes them very expressive models that can learn very complicated relationships between their inputs and outputs. With limited training data, however, many of these complicated relationships will be the result of sampling noise, so they will exist in the training set but not in real test data even if it is drawn from the same distribution. This leads to overfitting, and many methods have been developed for reducing it. These include stopping the training as soon as performance on a validation set starts to get worse, introducing weight penalties of various kinds such as L1 and L2 regularization and soft weight sharing (Nowlan and Hinton, 1992).

A motivating approach is model combination: averaging the predictions of many separately trained large neural networks is known to reduce variance. But this is prohibitively expensive for deep nets. Dropout addresses this by approximating the geometric mean of exponentially many thinned networks in a single training run.

## Methods

### The Core Idea

During training, each unit in the network is retained with probability $p$ (and dropped with probability $1-p$) independently for each training example. Typical values are $p = 0.5$ for hidden units and $p = 0.8$ for input units. The dropped units are temporarily removed from the network along with all their incoming and outgoing connections.

Formally, for a hidden layer activation $y$, dropout computes $\tilde{y} = r \odot y$ where $r \sim \text{Bernoulli}(p)$. The thinned network is trained with standard backpropagation.

### Test-Time Behavior

At test time, no dropout is applied. Instead, each weight is scaled by the retention probability $p$ (so that expected activation matches training time). Equivalently, one can scale activations by $1/p$ during training (inverted dropout) and use the full network unchanged at test time.

This corresponds to using a single neural network at test time whose output approximates the average prediction of all $2^n$ thinned sub-networks (where $n$ is the number of droppable units).

### Implementation Details

- Dropout is typically applied to hidden layers and sometimes to inputs.
- Higher learning rates (10-100x) and higher momentum (0.95-0.99) are typically used with dropout; max-norm regularization (bounding $\|w\|_2 \le c$) further helps training stability.
- Dropout is complementary to data augmentation; combining them gives additional gains.
- Dropout at convolutional layers is typically less effective than at fully-connected layers. The authors recommend applying dropout primarily at the top fully-connected layers of ConvNets.

### Intuition and Theory

The authors argue that dropout prevents co-adaptation of feature detectors: a unit cannot rely on the presence of particular other units, so each unit must individually be useful. This drives the network toward more robust, less entangled representations.

A theoretical connection is drawn to ensemble learning: dropout approximates a geometric mean of $2^n$ networks. For a single layer with sigmoid/softmax nonlinearity, the weight-scaling rule provides an exact equivalent to the geometric mean.

## Key Results

Dropout produces substantial improvements across many datasets:

- **MNIST**: Best dropout+max-norm convolutional network achieves 0.79% error rate, state-of-the-art at the time.
- **CIFAR-10**: Dropout reduces error from 14.98% (without dropout) to 12.61%, with data augmentation bringing it to 9.32%.
- **CIFAR-100**: Reduces error from 43.48% to 37.20%.
- **TIMIT** (phoneme recognition): Phone error rate drops from 23.4% (without dropout) to 21.8%.
- **Reuters-21578** (document classification): Substantial improvement in classification accuracy.
- **ImageNet** (ILSVRC-2010): Top-5 error drops from 48.6% (without dropout) to 42.4% using the Krizhevsky-style AlexNet architecture.

The paper also shows that dropout-trained networks have more sparse, less correlated hidden activations and more dispersed feature detectors, supporting the co-adaptation hypothesis.

## Conclusion

Dropout is a simple but strong regularization method. It is effective on a wide variety of architectures (fully-connected, convolutional, RBM-pretrained) and tasks (vision, speech, text, computational biology). Dropout's success paved the way for training much larger neural networks without severe overfitting and has become a standard component in many deep learning architectures. Its ensemble interpretation has also inspired related methods like DropConnect, DropPath, and stochastic depth. Limitations include slower training convergence (often 2-3x more epochs) and reduced effectiveness on some convolutional architectures.

## References

- Hinton et al. (2012) — Improving neural networks by preventing co-adaptation of feature detectors
- Krizhevsky et al. (2012) — ImageNet Classification with Deep Convolutional Neural Networks (AlexNet)
- Nowlan and Hinton (1992) — Simplifying Neural Networks by Soft Weight Sharing
- Bishop (1995) — Training with Noise is Equivalent to Tikhonov Regularization
- Wager et al. (2013) — Dropout Training as Adaptive Regularization
- Kingma and Ba (2014) — Adam: A Method for Stochastic Optimization
