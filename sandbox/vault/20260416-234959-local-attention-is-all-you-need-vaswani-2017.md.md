# Attention Is All You Need

**Authors:** Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Lukasz Kaiser, Illia Polosukhin (2017)
**arXiv:** 1706.03762

## Abstract

The dominant sequence transduction models at the time of publication were based on complex recurrent or convolutional neural networks that included an encoder and a decoder. The best performing models also connected the encoder and decoder through an attention mechanism. This paper proposes a new simple network architecture, the Transformer, based solely on attention mechanisms and dispensing with recurrence and convolutions entirely. Experiments on two machine translation tasks show these models to be superior in quality while being more parallelizable and requiring significantly less time to train. The Transformer achieves 28.4 BLEU on the WMT 2014 English-to-German translation task, improving over the existing best results, including ensembles, by over 2 BLEU. On the WMT 2014 English-to-French translation task, the model establishes a new single-model state-of-the-art BLEU score of 41.8 after training for 3.5 days on eight GPUs, a small fraction of the training costs of the best models from the literature. The Transformer generalizes well to other tasks by applying it successfully to English constituency parsing both with large and limited training data. The paper argues that attention-based architectures could replace recurrent models for many sequence tasks, and introduces scaled dot-product attention and multi-head attention as primitives that later became ubiquitous in deep learning. The architecture relies on positional encodings to inject order information into the otherwise permutation-equivariant attention computation. The Transformer has become foundational for subsequent language modeling work, including large-scale pretrained models.

## Methods

The Transformer follows an encoder-decoder structure, but replaces recurrent layers with stacks of self-attention and point-wise fully connected layers. The encoder is composed of a stack of N = 6 identical layers, each having two sub-layers: a multi-head self-attention mechanism, and a position-wise fully connected feed-forward network. Residual connections are applied around each sub-layer, followed by layer normalization. The decoder is also composed of a stack of N = 6 identical layers, with an additional third sub-layer that performs multi-head attention over the output of the encoder stack. The self-attention sub-layer in the decoder is masked to prevent positions from attending to subsequent positions, preserving the autoregressive property.

The central primitive is scaled dot-product attention: given queries Q, keys K, and values V, the attention output is softmax(QK^T / sqrt(d_k)) V, where d_k is the dimensionality of the keys. The sqrt(d_k) scaling counteracts the effect of large dot products that push softmax into regions with extremely small gradients. Multi-head attention projects the queries, keys, and values h times with different learned linear projections into lower-dimensional subspaces, applies attention in parallel in each head, and concatenates the results. For the base model, h = 8 heads are used with d_model = 512 and d_k = d_v = 64.

The position-wise feed-forward networks apply two linear transformations with a ReLU in between, with an inner dimensionality of 2048. Since the architecture contains no recurrence or convolution, sinusoidal positional encodings of varying frequencies are added to the input embeddings at the bottoms of the encoder and decoder stacks. Learned positional embeddings yielded nearly identical results.

Training uses the Adam optimizer with beta_1 = 0.9, beta_2 = 0.98, and epsilon = 10^-9, with a learning rate schedule that increases linearly for the first 4000 warmup steps and then decreases proportionally to the inverse square root of the step number. Regularization consists of residual dropout with rate 0.1 applied to the output of each sub-layer before it is added to the sub-layer input, and label smoothing with epsilon_ls = 0.1. The authors also introduce byte-pair encoding and a shared source-target vocabulary of about 37,000 tokens for the English-German task.

## Key Results

On WMT 2014 English-to-German, the big Transformer model achieves a BLEU score of 28.4, outperforming all previously published models and ensembles by more than 2.0 BLEU. The base model surpasses all previously published models at a fraction of the training cost. On WMT 2014 English-to-French, the big Transformer achieves 41.8 BLEU, a new single-model state-of-the-art, with training requiring 3.5 days on 8 NVIDIA P100 GPUs. Training cost estimates in FLOPs are roughly 2.3 * 10^19 for the big model, which is more than an order of magnitude less than the next-best ensemble model.

Ablation experiments on the English-to-German development set (newstest2013) examine the effects of varying the number of heads, key dimensions, model size, dropout rate, and positional encoding type. Single-head attention is 0.9 BLEU worse than the best multi-head setup, while too many heads also degrades quality. Reducing d_k alone also hurts performance, suggesting that determining compatibility is not trivial and a more sophisticated compatibility function than the dot product may be beneficial. Larger models perform better, and dropout is very helpful in avoiding overfitting.

On English constituency parsing using the Wall Street Journal portion of the Penn Treebank, a 4-layer Transformer with d_model = 1024 achieves an F1 score of 91.3 in the WSJ-only setting and 92.7 in the semi-supervised setting, outperforming previously reported results for the RNN Grammar and nearly matching the Berkeley parser even though it has not been tuned specifically for parsing.

## Conclusion

The Transformer is the first sequence transduction model based entirely on attention, replacing the recurrent layers most commonly used in encoder-decoder architectures with multi-head self-attention. For translation tasks, the Transformer can be trained significantly faster than architectures based on recurrent or convolutional layers, achieving new state-of-the-art results on WMT 2014 English-to-German and English-to-French translation. The authors suggest that attention-based models are promising for a range of tasks beyond translation, and plan to extend the Transformer to modalities beyond text and to investigate local, restricted attention mechanisms for handling large inputs and outputs.

## References

- Bahdanau et al. 2014 (neural machine translation by jointly learning to align and translate)
- Sutskever et al. 2014 (sequence to sequence learning with neural networks)
- Cho et al. 2014 (learning phrase representations using RNN encoder-decoder)
- Luong et al. 2015 (effective approaches to attention-based neural machine translation)
- Gehring et al. 2017 (convolutional sequence to sequence learning)
- Wu et al. 2016 (Google's neural machine translation system)
