# Aurevia Health

A production-shaped healthcare payer demo inspired by common member experiences. It uses fictional data and branding and is not affiliated with Humana.

## What is included

- Responsive member portal with coverage, claims, care gaps, digital ID card, and provider search
- Four independently deployable Node.js microservices
- Nginx API gateway and static web host
- Docker Compose deployment designed for a single Azure Linux VM
- Health endpoints and restart policies

## Architecture

```text
Browser -> Nginx gateway (:80) -> member-service (:3001)
                              -> claims-service (:3002)
                              -> provider-service (:3003)
                              -> care-service (:3004)
```

Only port 80 is exposed. Service ports remain inside the Docker network.

## Run locally

Install Docker Desktop, then from this folder run:

```bash
docker compose up --build
```

Open `http://localhost`. Stop with `docker compose down`.

## Deploy to an Azure VM

1. Create an Ubuntu 24.04 LTS VM (B2s is sufficient for the demo).
2. In its network security group, allow inbound TCP 22 from your IP and TCP 80 from the internet.
3. Connect with SSH and install Docker:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
```

4. Sign out and reconnect, copy this project to the VM, then run:

```bash
cd aurevia-health
docker compose up -d --build
docker compose ps
curl http://localhost/health
```

Open `http://<VM_PUBLIC_IP>`.

## Before using beyond a demo

This sample intentionally uses in-memory fictional data and has no authentication. A real healthcare deployment needs identity and role-based access, TLS, encrypted databases, secrets in Azure Key Vault, audit logs, monitoring, backups, vulnerability scanning, consent and retention policies, and an organization-specific HIPAA/HITRUST assessment. Avoid putting PHI in logs. For scale, the containers can later move to Azure Container Apps or AKS and use Azure API Management, Entra External ID, Azure SQL/PostgreSQL, and Application Insights.

## API endpoints

- `GET /api/member/`
- `GET /api/claims/`
- `GET /api/providers/`
- `GET /api/care/`
- `GET /health`
