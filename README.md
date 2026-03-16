# CS218 Assignment 3 — Containers & Cloud-Native Deployment

## Demo Video
<!-- Add your YouTube/video link here -->

---

## Local Setup Steps

### Prerequisites
- Docker Desktop installed and running
- `k6` installed (`brew install k6`)

### 1. Clone the repository
```bash
git clone <your-repo-url>
cd CS218-Assignment3
```

### 2. Create your local environment file
```bash
cp .env.example .env
```
Open `.env` and set a password:
```
POSTGRES_DB=orders
POSTGRES_USER=orders_user
POSTGRES_PASSWORD=your_password_here
```
> `.env` is gitignored and never committed. See **Secrets Handling** below.

---

## Docker Compose Instructions

### Start the full stack
```bash
docker compose up -d --build
```

### Run migrations (first time only)
```bash
docker compose run --rm api alembic upgrade head
```

### Verify health
```bash
curl -i http://localhost:8080/health
# Expected: {"status":"ok","db":"connected"}
```

### Create an order
```bash
curl -s -X POST http://localhost:8080/orders \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: test-001" \
  -d '{"customer_id":"cust1","item_id":"item1","quantity":2}'
```

### Fetch an order
```bash
curl -s http://localhost:8080/orders/<order_id>
```

### Stop the stack
```bash
docker compose down
```

---

## How Migrations Are Executed

Migrations use **Alembic**. The migration files live in `migrations/versions/`.

### Locally
```bash
docker compose run --rm api alembic upgrade head
```

### On AWS (against RDS)
```bash
docker run --rm \
  -e DB_HOST=cs218-orders-db.cp6wa6emww29.us-east-2.rds.amazonaws.com \
  -e DB_PORT=5432 \
  -e DB_NAME=orders \
  -e DB_USER=orders_user \
  -e DB_PASSWORD=<password> \
  375291433032.dkr.ecr.us-east-2.amazonaws.com/cs218-orders-api:latest \
  alembic upgrade head
```

---

## Secrets Handling

### Local
- Secrets live in `.env` which is **gitignored** and never committed
- `docker-compose.yml` reads vars via `${VAR}` syntax
- `.env.example` is committed as a safe template with no real values

### AWS
- Non-secret config (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`) are plain ECS task environment variables
- `DB_PASSWORD` is stored in **SSM Parameter Store** at `/cs218/orders/db-password` as a `SecureString`
- The ECS Task Execution Role has `AmazonSSMReadOnlyAccess` to fetch it at container startup
- No secrets exist in the Docker image or git history

---

## AWS Deployment Steps

### 1. Push image to ECR
```bash
aws ecr create-repository --repository-name cs218-orders-api --region us-east-2

aws ecr get-login-password --region us-east-2 | \
  docker login --username AWS --password-stdin \
  375291433032.dkr.ecr.us-east-2.amazonaws.com

docker build --platform linux/amd64 -t cs218-orders-api .

docker tag cs218-orders-api:latest \
  375291433032.dkr.ecr.us-east-2.amazonaws.com/cs218-orders-api:latest

docker push \
  375291433032.dkr.ecr.us-east-2.amazonaws.com/cs218-orders-api:latest
```

### 2. Store DB password in SSM
```bash
aws ssm put-parameter \
  --name "/cs218/orders/db-password" \
  --value "<password>" \
  --type SecureString \
  --region us-east-2
```

### 3. Create RDS Postgres
```bash
aws rds create-db-subnet-group \
  --db-subnet-group-name cs218-orders-subnet-group \
  --db-subnet-group-description "CS218 Orders API subnet group" \
  --subnet-ids subnet-0f859e4a09ee2edac subnet-0aa461c9900829dad subnet-0ada00e21a386ced2 \
  --region us-east-2

aws rds create-db-instance \
  --db-instance-identifier cs218-orders-db \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --engine-version 16.6 \
  --master-username orders_user \
  --master-user-password <password> \
  --db-name orders \
  --allocated-storage 20 \
  --no-multi-az \
  --publicly-accessible \
  --vpc-security-group-ids sg-0d8645942e95ec25a \
  --db-subnet-group-name cs218-orders-subnet-group \
  --backup-retention-period 0 \
  --no-deletion-protection \
  --region us-east-2
```

### 4. Create ECS cluster, IAM role, and CloudWatch log group
```bash
aws ecs create-cluster --cluster-name cs218-orders-cluster --region us-east-2

aws iam create-role \
  --role-name cs218EcsTaskExecutionRole \
  --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]
  }'

aws iam attach-role-policy \
  --role-name cs218EcsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

aws iam attach-role-policy \
  --role-name cs218EcsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMReadOnlyAccess

aws logs create-log-group --log-group-name /ecs/cs218-orders-api --region us-east-2
```

### 5. Register ECS task definition
```bash
aws ecs register-task-definition \
  --family cs218-orders-api \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu 256 \
  --memory 512 \
  --execution-role-arn arn:aws:iam::375291433032:role/cs218EcsTaskExecutionRole \
  --container-definitions file://container-definitions.json \
  --region us-east-2
```

### 6. Create ALB, target group, and listener
```bash
aws elbv2 create-load-balancer \
  --name cs218-orders-alb \
  --subnets subnet-0f859e4a09ee2edac subnet-0aa461c9900829dad subnet-0ada00e21a386ced2 \
  --security-groups sg-0d8645942e95ec25a \
  --scheme internet-facing \
  --type application \
  --region us-east-2

aws elbv2 create-target-group \
  --name cs218-orders-tg \
  --protocol HTTP \
  --port 8080 \
  --vpc-id vpc-097cded22e762e52e \
  --target-type ip \
  --health-check-path /health \
  --health-check-interval-seconds 15 \
  --health-check-timeout-seconds 5 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --region us-east-2

aws elbv2 create-listener \
  --load-balancer-arn arn:aws:elasticloadbalancing:us-east-2:375291433032:loadbalancer/app/cs218-orders-alb/36d3f63c6c31d2c7 \
  --protocol HTTP \
  --port 80 \
  --default-actions Type=forward,TargetGroupArn=arn:aws:elasticloadbalancing:us-east-2:375291433032:targetgroup/cs218-orders-tg/838088b58a963a3b \
  --region us-east-2
```

### 7. Open security group ports
```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-0d8645942e95ec25a --protocol tcp --port 80 --cidr 0.0.0.0/0 --region us-east-2

aws ec2 authorize-security-group-ingress \
  --group-id sg-0d8645942e95ec25a --protocol tcp --port 8080 --cidr 0.0.0.0/0 --region us-east-2

aws ec2 authorize-security-group-ingress \
  --group-id sg-0d8645942e95ec25a --protocol tcp --port 5432 --cidr 0.0.0.0/0 --region us-east-2
```

### 8. Create ECS service
```bash
aws ecs create-service \
  --cluster cs218-orders-cluster \
  --service-name cs218-orders-service \
  --task-definition cs218-orders-api:2 \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-0f859e4a09ee2edac,subnet-0aa461c9900829dad],securityGroups=[sg-0d8645942e95ec25a],assignPublicIp=ENABLED}" \
  --load-balancers "targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:375291433032:targetgroup/cs218-orders-tg/838088b58a963a3b,containerName=api,containerPort=8080" \
  --health-check-grace-period-seconds 60 \
  --region us-east-2
```

### 9. Run migrations against RDS
```bash
docker run --rm \
  -e DB_HOST=cs218-orders-db.cp6wa6emww29.us-east-2.rds.amazonaws.com \
  -e DB_PORT=5432 -e DB_NAME=orders -e DB_USER=orders_user -e DB_PASSWORD=<password> \
  375291433032.dkr.ecr.us-east-2.amazonaws.com/cs218-orders-api:latest \
  alembic upgrade head
```

---

## Public ALB URL

```
http://cs218-orders-alb-790626801.us-east-2.elb.amazonaws.com
```

---

## ECS Service Name

```
cs218-orders-service
```

---

## Database Type

**RDS Postgres 16.6** (managed, not a container)

---

## Instance Types

| Component | Type |
|---|---|
| ECS Fargate Task | 256 CPU / 512 MB memory |
| RDS Postgres | db.t3.micro |

---

## Load Test Summary

**Tool:** k6
**Script:** `loadtest.js`

**Run:**
```bash
k6 run loadtest.js
```

**Configuration:** 20 VUs — 30s ramp-up, 60s sustained, 15s ramp-down (~105s total)

| Metric | Value |
|---|---|
| Total Requests | 6,283 |
| RPS (avg) | 59.72 req/s |
| Failed Requests | 0.00% |
| p90 latency | 37.41ms |
| p95 latency | 42.27ms |
| POST /orders p95 | 48.04ms |
| GET /orders/:id p95 | 27.92ms |
| Orders Created | 3,005 |
| Orders Fetched | 3,005 |

**Analysis:** At 20 VUs the API sustained ~60 RPS with p95 latency of 42ms and 0% errors. The bottleneck is the Postgres write path — POST p95 (48ms) is higher than GET p95 (28ms) because each POST performs 3 atomic inserts (orders, ledger, idempotency_records). CPU and network were not limiting factors at this concurrency level.

---