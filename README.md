# CS218 Assignment 3 — Containers & Cloud-Native Deployment

## Demo Video
https://youtu.be/Conyy7Mn9eY
---

## Local Setup Steps

### Prerequisites
- Docker Desktop installed and running
- `k6` installed (`brew install k6`)

### 1. Clone the repository
```bash
git clone https://github.com/Mingkun0906/CS218-Assignment3.git
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
`.env` is gitignored and never committed.

---

## Docker Compose & Local Test Scenarios

### Step 1 — Start the full stack
```bash
docker compose up -d --build
```

### Step 2 — Run migrations
How Migrations Are Executed
Migrations use **Alembic**. The migration files live in `migrations/versions/`.

```bash
docker compose run --rm api alembic upgrade head
```

### Step 3 — Verify health (Test 1)
```bash
curl -i http://localhost:8080/health
```
Expected: `HTTP 200` with `{"status":"ok","db":"connected"}`

---

### Step 4 — Persistence across API restart (Test 2)
```bash
curl -s -X POST http://localhost:8080/orders \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: persist-test-1" \
  -d '{"customer_id":"cust1","item_id":"item1","quantity":2}'

docker compose restart api

curl -s http://localhost:8080/orders/<order_id_from_above>
```
Expected: Record still exists after API restart.

---

### Step 5 — Postgres volume persistence (Test 3)
```bash
docker compose restart postgres

curl -s http://localhost:8080/orders/<order_id_from_above>
```
Expected: Record still exists after Postgres restart.

---

### Step 6 — Load test (Test 6)
```bash
k6 run loadtest.js
```
Expected: Near 0% failed requests. See **Load Test Summary** for results.

---

### Stop the stack
```bash
docker compose down
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
  --load-balancer-arn arn:aws:elasticloadbalancing:us-east-2:375291433032:loadbalancer/app/cs218-orders-alb/0c90b76fedaff9c9 \
  --protocol HTTP \
  --port 80 \
  --default-actions Type=forward,TargetGroupArn=arn:aws:elasticloadbalancing:us-east-2:375291433032:targetgroup/cs218-orders-tg/f661db8a90d2dc8f \
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
  --task-definition cs218-orders-api:3 \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-0f859e4a09ee2edac,subnet-0aa461c9900829dad],securityGroups=[sg-0d8645942e95ec25a],assignPublicIp=ENABLED}" \
  --load-balancers "targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:375291433032:targetgroup/cs218-orders-tg/f661db8a90d2dc8f,containerName=api,containerPort=8080" \
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

## AWS Test Scenarios

### Test 4 — AWS Health Endpoint via ALB
```bash
BASE_URL=http://cs218-orders-alb-78067180.us-east-2.elb.amazonaws.com
curl -i $BASE_URL/health
```
Expected: `HTTP 200` with `{"status":"ok","db":"connected"}`

---

### Test 5 — AWS Write + Read Verification (Proof of Postgres)
```bash
BASE_URL=http://cs218-orders-alb-78067180.us-east-2.elb.amazonaws.com

# Write
curl -s -X POST $BASE_URL/orders \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: cloud-test-001" \
  -d '{"customer_id":"cust1","item_id":"item1","quantity":3}'

# Read back using returned order_id
curl -s $BASE_URL/orders/<order_id>
```
Expected: POST returns `order_id`; GET returns the same record from RDS.

---

### CloudWatch Logs (live tail)
```bash
aws logs tail /ecs/cs218-orders-api --follow --region us-east-2
```
Press `Ctrl+C` to stop. Shows all requests hitting the ECS container in real time.

---

## Public ALB URL

```
http://cs218-orders-alb-78067180.us-east-2.elb.amazonaws.com
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


========================================
  k6 Load Test Summary — Orders API
========================================
Duration       : ~105s (30s ramp-up, 60s sustained, 15s ramp-down)
Max VUs        : 20

--- HTTP Overview ---
Total requests : 6194
RPS (avg)      : 58.77 req/s
Failed requests: 0.00%

--- Latency (all requests) ---
p50            : 0.00ms
p90            : 48.44ms
p95            : 63.59ms
p99            : 0.00ms

--- POST /orders latency ---
p50            : 0.00ms
p95            : 75.78ms

--- GET /orders/:id latency ---
p50            : 0.00ms
p95            : 43.88ms

--- Counters ---
Orders created : 2949
Orders fetched : 2949

========================================




---

## Cleanup (run after demo)


```bash
# ECS
aws ecs update-service --cluster cs218-orders-cluster --service cs218-orders-service --desired-count 0 --region us-east-2
aws ecs delete-service --cluster cs218-orders-cluster --service cs218-orders-service --region us-east-2
aws ecs delete-cluster --cluster cs218-orders-cluster --region us-east-2

# ALB
aws elbv2 delete-listener --listener-arn arn:aws:elasticloadbalancing:us-east-2:375291433032:listener/app/cs218-orders-alb/0c90b76fedaff9c9/7c66c46942f1bb22 --region us-east-2
aws elbv2 delete-load-balancer --load-balancer-arn arn:aws:elasticloadbalancing:us-east-2:375291433032:loadbalancer/app/cs218-orders-alb/0c90b76fedaff9c9 --region us-east-2
aws elbv2 delete-target-group --target-group-arn arn:aws:elasticloadbalancing:us-east-2:375291433032:targetgroup/cs218-orders-tg/f661db8a90d2dc8f --region us-east-2

# RDS
aws rds delete-db-instance --db-instance-identifier cs218-orders-db --skip-final-snapshot --region us-east-2
aws rds delete-db-subnet-group --db-subnet-group-name cs218-orders-subnet-group --region us-east-2

# ECR
aws ecr delete-repository --repository-name cs218-orders-api --force --region us-east-2

# SSM + CloudWatch
aws ssm delete-parameter --name "/cs218/orders/db-password" --region us-east-2
aws logs delete-log-group --log-group-name /ecs/cs218-orders-api --region us-east-2
```