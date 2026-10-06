# model

The trained Muneer model. **Its weights are private and not published:** this folder holds only this README. The
weights, the tokenizer and the training data belong to the project owner ([LICENSE](../LICENSE)).

- **What it is:** a full fine-tune of [Qwen3-14B](https://huggingface.co/Qwen/Qwen3-14B), every weight trained (no
  LoRA), in bfloat16. How it was trained, and the run that produced it, are in the [training README](../training/README.md).
- **Where it is:** a private Hugging Face repository, which the model server downloads at startup
  ([server](../server/README.md), [deploy, step 1](../deploy/README.md#1-put-the-weights-in-a-private-hugging-face-repo)).

![The private Hugging Face repository holding Muneer, marked "private", 29.6 GB in all. Its latest commit is "Muneer: new training run" (2bbb250). Its files: model.safetensors (29.5 GB), the tokenizer (tokenizer.json, tokenizer_config.json, chat_template.jinja), config.json and generation_config.json, and the training run's records (trainer_state.json, training_args.bin, tokens_state.json). The owner's account name is hidden.](../docs/images/model-repo.png)

## What the folder holds (not published)

The same files as the repository:

| File | Size | What it is | Downloaded by the server |
|---|---|---|---|
| `model.safetensors` | 29.5 GB | The weights | Yes |
| `config.json` | 1.7 KB | The architecture: Qwen3, 40 layers | Yes |
| `generation_config.json` | 188 B | The model's default sampling | Yes |
| `tokenizer.json` | 11.4 MB | The tokenizer | Yes |
| `tokenizer_config.json` | 711 B | The tokenizer's settings | Yes |
| `chat_template.jinja` | 752 B | The ChatML template the model was trained with | Yes; the server uses [its own](../server/chat_template.jinja) |
| `trainer_state.json` | 2.8 MB | The training log: the loss, learning rate and gradient norm at every step, and every evaluation | No |
| `training_args.bin` | 7.4 KB | The settings the trainer ran with | No |
| `tokens_state.json` | 44 B | The number of tokens processed | No |
