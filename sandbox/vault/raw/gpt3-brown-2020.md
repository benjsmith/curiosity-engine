# Language Models are Few-Shot Learners

**Authors:** Tom B. Brown, Benjamin Mann, Nick Ryder, Melanie Subbiah, Jared Kaplan, Prafulla Dhariwal, Arvind Neelakantan, Pranav Shyam, Girish Sastry, Amanda Askell, Sandhini Agarwal, Ariel Herbert-Voss, Gretchen Krueger, Tom Henighan, Rewon Child, Aditya Ramesh, Daniel M. Ziegler, Jeffrey Wu, Clemens Winter, Christopher Hesse, Mark Chen, Eric Sigler, Mateusz Litwin, Scott Gray, Benjamin Chess, Jack Clark, Christopher Berner, Sam McCandlish, Alec Radford, Ilya Sutskever, Dario Amodei (2020)
**arXiv:** 2005.14165

## Abstract

Recent work has demonstrated substantial gains on many NLP tasks and benchmarks by pre-training on a large corpus of text followed by fine-tuning on a specific task. While typically task-agnostic in architecture, this method still requires task-specific fine-tuning datasets of thousands or tens of thousands of examples. By contrast, humans can generally perform a new language task from only a few examples or from simple instructions. This paper shows that scaling up language models greatly improves task-agnostic, few-shot performance, sometimes even reaching competitiveness with prior state-of-the-art fine-tuning approaches. Specifically, the authors train GPT-3, an autoregressive language model with 175 billion parameters, 10x more than any previous non-sparse language model, and test its performance in the few-shot setting. For all tasks, GPT-3 is applied without any gradient updates or fine-tuning, with tasks and few-shot demonstrations specified purely via text interaction with the model. GPT-3 achieves strong performance on many NLP datasets, including translation, question answering, and cloze tasks, as well as on several tasks that require on-the-fly reasoning or domain adaptation, such as unscrambling words, using a novel word in a sentence, or performing three-digit arithmetic. At the same time, the authors identify some datasets where GPT-3's few-shot learning still struggles, as well as some datasets where GPT-3 faces methodological issues related to training on large web corpora. Finally, the paper discusses broader societal impacts of the technology and of large language models in general, including potential misuse and biases.

## Methods

GPT-3 uses the same model architecture as GPT-2 (Radford et al. 2019), which is a decoder-only autoregressive Transformer, with modifications including the modified initialization, pre-normalization, and reversible tokenization described there, plus alternating dense and locally banded sparse attention patterns similar to the Sparse Transformer. To study the dependence of performance on model size, eight models are trained ranging from 125 million parameters to 175 billion parameters, spanning three orders of magnitude. The largest model, called GPT-3, has 96 layers, 12,288 hidden units per layer, 96 attention heads of dimension 128, and a context window of 2048 tokens. All models use a vocabulary of approximately 50,000 BPE tokens.

Training data consists of a filtered version of Common Crawl plus several curated high-quality corpora, including WebText2, Books1, Books2, and English Wikipedia. Common Crawl is filtered using a classifier trained to distinguish high-quality text from low-quality documents, and fuzzy deduplication is applied both within and across datasets to reduce overlap. The final mixture totals approximately 300 billion training tokens, and higher-quality datasets are upsampled relative to their raw size during training.

The key methodological contribution is the systematic study of three evaluation regimes that require no gradient updates. Zero-shot involves giving the model only a natural language description of the task and expecting it to produce the answer. One-shot provides a single input-output demonstration in the context, followed by the target query. Few-shot includes K demonstrations in the context window, where K can range from 10 to 100 depending on the task and the context limit. This approach is referred to as in-context learning: the model is not updated at all, but instead uses the prompt to infer the task structure.

Training is performed with Adam, beta_1 = 0.9, beta_2 = 0.95, epsilon = 10^-8, with gradient norm clipping at 1.0, cosine decay of the learning rate to 10% of its initial value over 260 billion tokens, and a linear warmup over the first 375 million tokens. Batch size is gradually increased from 32,000 tokens to 3.2 million tokens over the first 4-12 billion tokens of training. The largest model is trained on a high-bandwidth cluster provided by Microsoft, using a mixture of model and data parallelism across V100 GPUs. Training compute is estimated at several thousand petaflop/s-days for the 175B model.

Evaluation covers over two dozen datasets across categories including language modeling (LAMBADA, HellaSwag, StoryCloze), closed-book question answering (TriviaQA, Natural Questions, WebQs), translation, Winograd-style tasks, commonsense reasoning, reading comprehension (SQuAD, CoQA, DROP), SuperGLUE, natural language inference, and a suite of synthetic tasks including arithmetic, word unscrambling, and SAT analogies.

## Key Results

GPT-3 sets new state-of-the-art results on several benchmarks in the few-shot setting. On LAMBADA, it achieves 86.4% accuracy few-shot, surpassing the prior fine-tuned state-of-the-art by a wide margin. On TriviaQA, GPT-3 reaches 71.2% accuracy in the closed-book few-shot setting, exceeding the fine-tuned open-domain state-of-the-art at the time. On the PIQA physical reasoning benchmark, GPT-3 achieves 82.8% zero-shot and similar few-shot, outperforming fine-tuned baselines.

On SuperGLUE, GPT-3 few-shot scores 71.8 on average with 32 examples per task in context, roughly 4 points below a fine-tuned BERT-Large baseline. On translation tasks (WMT 14, 16), GPT-3 few-shot outperforms prior unsupervised NMT results when translating into English, but lags behind supervised systems when translating out of English. For reading comprehension, GPT-3 underperforms fine-tuned models, especially on tasks that require numeric reasoning such as DROP.

On synthetic arithmetic tasks, GPT-3 performs two-digit addition with near-perfect accuracy, three-digit addition with 80-90% accuracy, and struggles on four- and five-digit arithmetic. It achieves high performance on word unscrambling and novel-word-use tasks. Scaling trends show smooth, often log-linear improvement with model size for most tasks, with some evidence of more abrupt gains on certain reasoning tasks.

The paper also reports limitations. GPT-3 shows weaknesses on tasks that require bidirectional reasoning, struggles with some commonsense physics tasks like ANLI, and suffers from a tendency to generate text that repeats, contradicts itself, or loses coherence over long passages. The authors also analyze memorization of benchmark test sets, gender and race biases, energy use, and potential for misuse in generating misleading text.

## Conclusion

GPT-3 provides strong evidence that large-scale autoregressive language models, when trained on broad text corpora, acquire a substantial range of skills that can be elicited at inference time via natural language prompts and a handful of examples. The paper frames this capability as in-context learning and shows that performance improves smoothly with scale across many tasks. While GPT-3 is not uniformly superior to fine-tuned specialists, its breadth and flexibility without task-specific training data represent a significant advance and raise new research and societal questions about how to deploy and evaluate such models.

## References

- Vaswani et al. 2017 (Transformer architecture)
- Radford et al. 2018 (GPT-1 generative pre-training)
- Radford et al. 2019 (GPT-2 language models are unsupervised multitask learners)
- Devlin et al. 2018 (BERT bidirectional pre-training)
- Kaplan et al. 2020 (scaling laws for neural language models)
- Child et al. 2019 (sparse transformers for long sequences)
