# The public demo runs in one RunPod pod

The public demo needs a working link to the whole solution that keeps working for weeks. A laptop can't promise that. The demo therefore runs the model server and the app together in one RunPod GPU pod, which serves the app at the pod's public address. The split in [ADR 0003](0003-model-on-runpod-app-in-local-docker.md) stays for development.

One image (`deploy/all-in-one.Dockerfile`) holds vLLM and the app, each with its own Python. A small launcher (`deploy/all_in_one.py`) starts both, starts either again if it stops or stops answering, and stops both with the container. A pod runs a single container, so the launcher does the job Docker Compose does on a laptop.

## How it stays safe

- By default only the app's port is published. The model server listens on 127.0.0.1 inside the pod, with an API key made fresh at each start.
- To evaluate the model without a second deployment, the pod can open the model's API: setting `MUNEER_API_KEY` makes the server listen on its port behind that key (at least 32 characters), and the pod must also publish the port. Calls made this way skip the guardrail, so the key is kept as a secret and the port is published only when needed.
- The app faces the internet, so it runs as an unprivileged user and receives only the settings it uses: never the Hugging Face token, nor a secret added to the pod later. The folders with the weights are closed to it, and root never follows a link the app could have planted. The model server never receives the OpenAI key.
- The settings come from `.env.example`, built into the image; the pod's variables override them. The pod itself only needs its secrets and the weights' repo.

## Considered options

- **Two pods, one for the model and one for the app.** Two bills and two things to keep up, for no benefit: the app needs little CPU and no GPU.
- **The app on another host (a tunnel from the laptop, or a small cloud server).** The laptop must stay on for more than two weeks, or there is one more account and bill; either way, the model's API would be public again.
- **A separate model pod for evaluations.** A second deployment to set up and pay for, serving the same weights. Opening the demo pod's model port, behind a key, does the same job.

## Consequences

An app change means rebuilding and pushing the image, then restarting the pod, which reloads the model in a few minutes. While the demo is live, changes should be rare, so this is acceptable. Only Muneer's layers are pushed; Docker Hub reuses the vLLM base.
