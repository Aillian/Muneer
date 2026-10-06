# The model runs on RunPod; the rest runs in Docker on a laptop

Muneer is served by vLLM on a RunPod GPU pod, which downloads the ~30 GB of weights from a private Hugging Face repo. The pipeline and the Gradio app run in Docker on a laptop and call the model through its OpenAI-compatible API, protected by an API key.

With this split, app changes redeploy in seconds without reloading the model, and the GPU can be switched off when it isn't needed. During development the app talks to a scripted mock instead of the GPU.

## Considered options

- **One image with everything on the GPU pod.** This gives a single URL, but every app change restarts the pod and reloads the model.
- **One Docker Compose file for the whole system on RunPod.** Not possible: RunPod pods run a single container and cannot run Docker Compose.

## Consequences

The app image doesn't depend on its host. If the laptop can't stay online for the public demo, the image can move to a cloud host unchanged.

## Update

For the public demo, [ADR 0006](0006-public-demo-in-one-runpod-pod.md) runs the model and the app together in one RunPod pod. This split stays for development.
