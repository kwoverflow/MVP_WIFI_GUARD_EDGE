#!/bin/bash
# wifiguard-kafka (Amazon Linux 2023) — apache/kafka:3.9.1 KRaft 단일 노드를 docker run 으로 기동.
# advertised listener 는 이 인스턴스의 VPC 사설 IP(IMDSv2)로 잡는다. 백엔드 EC2는 사설 IP:9092 로 붙는다.
set -euxo pipefail

dnf update -y
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user

# t3.small(2GB) 보완: 1GB swap + JVM heap 768m
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
PRIV=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/local-ipv4)

docker run -d --name wg-kafka --restart unless-stopped -p 9092:9092 \
  -e KAFKA_NODE_ID=1 \
  -e KAFKA_PROCESS_ROLES=broker,controller \
  -e KAFKA_LISTENERS=PLAINTEXT://:9092,CONTROLLER://:9093 \
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://${PRIV}:9092 \
  -e KAFKA_CONTROLLER_LISTENER_NAMES=CONTROLLER \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT \
  -e KAFKA_CONTROLLER_QUORUM_VOTERS=1@localhost:9093 \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR=1 \
  -e KAFKA_TRANSACTION_STATE_LOG_MIN_ISR=1 \
  -e KAFKA_AUTO_CREATE_TOPICS_ENABLE=false \
  -e KAFKA_HEAP_OPTS="-Xmx768m -Xms768m" \
  -v wg-kafka-data:/var/lib/kafka/data apache/kafka:3.9.1

for i in $(seq 1 45); do
  docker exec wg-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list >/dev/null 2>&1 && break; sleep 2
done
# 명세 backend §7.2 토픽 3종 (compose 와 동일하게 auto-create 는 끄고 명시 생성)
for t in csi-feature-stream csi-telemetry csi-inference-result; do
  docker exec wg-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
    --create --if-not-exists --topic "$t" --partitions 3 --replication-factor 1
done
docker exec wg-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list > /var/log/wifiguard-userdata.done
