#! /bin/bash
mv PROD_docker-compose.yml /var/lib/bulk_sms/docker-compose.yml &&
mv PROD_global_config.ini /var/lib/bulk_sms/cluster_config/global_config.ini &&
mv setup_ip.py /var/lib/bulk_sms/ && cd /var/lib/bulk_sms/ &&
python3.6 setup_ip.py && docker-compose pull && docker-compose up -d