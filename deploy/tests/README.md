# deploy/tests

Tests for [`all_in_one.py`](../all_in_one.py), all in [`test_all_in_one.py`](test_all_in_one.py). They need no GPU, no
vLLM, no image build and no network: a pod's settings are a dictionary of dummy values.

## What they cover

- **Settings:** the pod's variables win over `.env.example`, and a missing or invalid setting stops the script early.
- **The plan:** who listens where, runs as whom, and gets which secrets.
- **The volume:** the database gets a folder of its own under `/workspace`, and the weights are closed to the app.
- **Restarts and stops:** the back-off delays, and every process and its workers stopped.
- **Health checks:** when Muneer is ready, and when a hung process is restarted.

## How they work

- **Real processes, fake clock:** the restart tests start short Python processes and move a fake clock forward, so no
  test waits out a restart delay.
- **Linux and root:** tests marked `posix_only` are skipped on Windows (they expect Linux's `/proc`), and those marked
  `root_only` also need root. The Linux test container runs them all.

## Run them

From the repository root:

```bash
python -m pytest deploy/tests
```

Every test in the repository, in Linux with the pinned versions:

```bash
docker compose -f docker-compose.test.yml run --rm tests
```
