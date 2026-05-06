# Deployment

PyFlue is a Python package, so deployment is centered on Python runtimes,
containers, and CI jobs.

The closest PyFlue equivalent to a JavaScript server target is the Docker and
FastAPI target. It produces a small `app.py` and `Dockerfile` that can run on
any platform that accepts a Python container.

## Supported Targets

| Target | Status | Command | Notes |
| --- | --- | --- | --- |
| **New Build System** |
| Uvicorn | Implemented | `pyflue build --target uvicorn` | FastAPI server with SSE/webhook support. |
| Lambda | Implemented | `pyflue build --target lambda` | AWS Lambda with Mangum adapter. |
| Cloud Run | Implemented | `pyflue build --target cloudrun` | Google Cloud Run with gunicorn. |
| Docker | Implemented | `pyflue build --target docker` | General Python container. |
| **CI/CD** |
| GitHub Actions | Implemented | `pyflue build --target github-actions` | Generates `.github/workflows/pyflue-agent.yml`. |
| GitLab CI/CD | Implemented | `pyflue build --target gitlab-ci` | Generates `.gitlab-ci.yml`. |
| **Platforms** |
| Railway | Implemented | `pyflue build --target railway` | Generates Docker/FastAPI artifacts and `railway.json`. |
| Render | Implemented | `pyflue build --target render` | Generates Docker/FastAPI artifacts and `render.yaml`. |
| Fly.io | Implemented | `pyflue build --target fly` | Generates Docker/FastAPI artifacts and `fly.toml`. |
| Vercel | Implemented | `pyflue build --target vercel` | Generates Python app artifacts and `vercel.json`. |
| Netlify | Implemented | `pyflue build --target netlify` | Generates Python app artifacts and `netlify.toml`. |
| Cloudflare Containers | Beta | `pyflue build --target cloudflare` | Generates Docker/FastAPI artifacts, `worker.ts`, `wrangler.jsonc`, and `package.json`. |

## Uvicorn/FastAPI

Generate a FastAPI server with built-in development support:

```bash
pyflue build --target uvicorn
```

This writes:

```text
dist/server.py
dist/requirements.txt
dist/manifest.json
```

Run the server:

```bash
cd dist
pip install -r requirements.txt
python server.py
```

The server exposes:
- `GET /health` - Health check
- `GET /agents` - List available agents
- `POST /agents/{name}/{agent_id}` - Run an agent

## AWS Lambda

Generate an AWS Lambda handler with Mangum adapter:

```bash
pyflue build --target lambda
```

This writes:

```text
dist/main.py
dist/requirements.txt
dist/manifest.json
```

Deploy to Lambda using the AWS CLI or SAM. The handler is `handler`.

## Google Cloud Run

Generate a Cloud Run optimized container:

```bash
pyflue build --target cloudrun
```

This writes:

```text
dist/server.py
dist/Dockerfile
dist/requirements.txt
dist/cloudbuild.yaml
dist/manifest.json
```

Deploy using:

```bash
gcloud run deploy --source .
```

## Docker/FastAPI

Generate the default Python web artifacts:

```bash
pyflue build --target docker
```

This writes:

```text
dist/Dockerfile
dist/server.py
dist/requirements.txt
dist/manifest.json
```

The generated server exposes the PyFlue server:

```bash
curl http://localhost:8000/prompt/default \
  -H "Content-Type: application/json" \
  -d '{"payload": {"prompt": "Review this project"}}'
```

File-based agents are exposed under `/agents/{name}/{agent_id}`.

## GitHub Actions

Generate a workflow:

```bash
pyflue build --target github-actions
```

This writes:

```text
.github/workflows/pyflue-agent.yml
```

The workflow is manual by default. Add provider keys as repository secrets,
then run it from the GitHub Actions tab.

## GitLab CI/CD

Generate a pipeline file:

```bash
pyflue build --target gitlab-ci
```

This writes:

```text
.gitlab-ci.yml
```

The generated job is designed for manual web pipelines. Add provider keys as
masked CI/CD variables.

## Railway

Generate Railway files:

```bash
pyflue build --target railway
```

This writes:

```text
Dockerfile
app.py
railway.json
```

Deploy the project with Railway's GitHub integration or CLI.

```bash
pyflue deploy --target railway
```

When the Railway CLI is installed and authenticated, PyFlue runs
`railway up`.

## Render

Generate Render files:

```bash
pyflue build --target render
```

This writes:

```text
Dockerfile
app.py
render.yaml
```

Create a new Blueprint in Render and point it at the repository.

## Fly.io

Generate Fly.io files:

```bash
pyflue build --target fly
```

This writes:

```text
Dockerfile
app.py
fly.toml
```

Review the generated app name and region before deploying with `fly deploy`.

```bash
pyflue deploy --target fly
```

When the Fly.io CLI is installed and authenticated, PyFlue runs `fly deploy`.

## Cloudflare Containers

Generate Cloudflare Containers files:

```bash
pyflue build --target cloudflare
```

This target generates:

```text
Dockerfile
app.py
worker.ts
wrangler.jsonc
package.json
```

Cloudflare Containers are currently a beta Workers feature and require a
Workers Paid plan. `wrangler deploy` builds and pushes the Docker image, so
Docker must be running locally during deployment.

Deploy with Wrangler:

```bash
npm install
npm run deploy
```

## Vercel

Generate Vercel files:

```bash
pyflue build --target vercel
```

This writes:

```text
Dockerfile
app.py
vercel.json
```

Review the generated route config before deploying.

```bash
pyflue deploy --target vercel
```

When the Vercel CLI is installed and authenticated, PyFlue runs
`vercel deploy`.

## Netlify

Generate Netlify files:

```bash
pyflue build --target netlify
```

This writes:

```text
Dockerfile
app.py
netlify.toml
```

Review the generated function settings before deploying.

```bash
pyflue deploy --target netlify
```

When the Netlify CLI is installed and authenticated, PyFlue runs
`netlify deploy`.
