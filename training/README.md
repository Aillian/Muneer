# training

The configuration of the run that produced the served model: a full fine-tune of Qwen3-14B on conversations in ChatML,
sharded across GPUs with PyTorch FSDP2. **The training data is private and not included**, so this folder documents how
the model was trained rather than letting anyone retrain it.

## What the folder holds

| File | What it is | Published |
|---|---|---|
| [`config.yaml`](config.yaml) | The training configuration: the base model, the datasets and their format, FSDP2 and the hyperparameters | Yes |
| `README.md` | This page | Yes |
| `training_data/part_1.jsonl` … `part_4.jsonl` | The training conversations, in four files | No: private ([LICENSE](../LICENSE)) |
| `upload_model_to_hf.ipynb` | The notebook that uploaded the weights to Hugging Face | No |

## The run that produced the served model

- **8× NVIDIA H200**, the weights sharded across them with FSDP2.
- **6 epochs were planned; the served checkpoint is the end of epoch 3**, step 6,576 of 13,152.
- **861.8 million tokens processed**, 725.6 million of them trained on (Muneer's replies).
- **The evaluation loss** on the 3% of conversations held out:

| Epoch | Step | Evaluation loss | Perplexity |
|---|---|---|---|
| 0 (before training) | 0 | 1.849 | 6.35 |
| 0.5 | 1,096 | 0.553 | 1.74 |
| 1 | 2,192 | 0.498 | 1.64 |
| 1.5 | 3,288 | 0.474 | 1.61 |
| 2 | 4,384 | 0.455 | 1.58 |
| 2.5 | 5,480 | 0.458 | 1.58 |
| 3 | 6,576 | **0.454** | **1.58** |

The run's charts, by training step: the evaluation loss, the training loss and perplexity, the learning rate, and the
gradient norm. The marked step, 6,576, is the served checkpoint; the run went on a little past it.

![Five charts of the training run "muneer-v1", by training step. Evaluation loss: from 1.85 at step 0 down to about 0.55 by step 1,100, then slowly to 0.454 at step 6,576. Training loss: from about 1.8 down to about 0.3, noisy but falling throughout. Training perplexity: from about 6.5 down to about 1.4. Learning rate: rising from 0 to 0.00005 over the first few hundred steps, then constant. Gradient norm: about 10 at the start, then below 1, with a few short spikes.](../docs/images/training-charts.png)

The weights themselves are private ([model](../model/README.md)).

## The data format

Each line of a dataset file is one conversation, in a `messages` list:

```json
{"messages": [{"role": "user", "content": "What is Islam?"}, {"role": "assistant", "content": "Islam means submission to the One God..."}]}
```

There are no system messages in the training data. When serving, the model server adds a short, fixed one
([ADR 0002](../docs/adr/0002-no-system-prompt-for-muneer.md)).
