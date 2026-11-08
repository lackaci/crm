"""Explicit launcher/download for the desktop's local-only document model."""
import shutil
import subprocess
import time

import httpx

from app.providers.document_model import DEFAULT_DOCUMENT_MODEL

ENDPOINT = "http://127.0.0.1:11434"
CONTAINER = "mini-crm-document-ollama"


def available():
    try:
        with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
            return client.get(ENDPOINT + "/api/tags").status_code == 200
    except httpx.HTTPError:
        return False


def main():
    if not available():
        docker = shutil.which("docker")
        if docker is None:
            raise SystemExit("Start the Ollama desktop app first, or install/start Docker Desktop, then run this launcher again.")
        inspected = subprocess.run([docker, "inspect", "--format", "{{.Config.Image}}", CONTAINER], capture_output=True, text=True)
        if inspected.returncode == 0:
            if inspected.stdout.strip() != "ollama/ollama:latest":
                raise SystemExit("The document runtime name belongs to another image. Start Ollama manually on localhost:11434.")
            subprocess.run([docker, "start", CONTAINER], check=True)
        else:
            subprocess.run([docker, "run", "-d", "--name", CONTAINER, "--memory=6g", "--cpus=4", "-p", "127.0.0.1:11434:11434",
                "-v", "mini_crm_document_models:/root/.ollama", "ollama/ollama:latest"], check=True)
        for _ in range(30):
            if available():
                break
            time.sleep(1)
        else:
            raise SystemExit("Ollama did not start. Check Docker Desktop and localhost port 11434.")
    print("Ensuring the local vision model is downloaded (about 1.9 GB on first setup)…", flush=True)
    with httpx.Client(timeout=httpx.Timeout(900, connect=5), trust_env=False, follow_redirects=False) as client:
        response = client.post(ENDPOINT + "/api/pull", json={"model": DEFAULT_DOCUMENT_MODEL, "stream": False})
        if response.status_code != 200 or response.json().get("status") != "success":
            raise SystemExit("Model download failed. Try: ollama pull " + DEFAULT_DOCUMENT_MODEL)
    print("Local document model ready. Open CRM > Files > Excel register.")


if __name__ == "__main__":
    main()
