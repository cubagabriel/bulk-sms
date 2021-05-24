FROM tiangolo/uwsgi-nginx:python3.8
ENV http_proxy 'http://10.30.17.74:443/'
ENV https_proxy 'http://10.30.17.74:443/'
#RUN mkdir -p /etc/ssl/
RUN apt-get update && \
    apt-get install -y openssl && \
    openssl genrsa -des3 -passout pass:j123jf74 -out /etc/ssl/server.pass.key 2048 && \
    openssl rsa -passin pass:j123jf74 -in /etc/ssl/server.pass.key -out /etc/ssl/server.key && \
    rm /etc/ssl/server.pass.key && \
    openssl req -new -key /etc/ssl/server.key -out /etc/ssl/server.csr \
        -subj "/C=PE/ST=Lima/L=Lima/O=Organization/OU=Redes/CN=bulksms" && \
    openssl x509 -req -days 365 -in /etc/ssl/server.csr -signkey /etc/ssl/server.key -out /etc/ssl/server.crt

ENV UWSGI_CHEAPER 0
ENV UWSGI_PROCESSES 1

# Set the folder where uwsgi looks for the app
WORKDIR /app

# Copy the app contents to the image
COPY nginx.conf /etc/nginx/conf.d/secure.conf
COPY ./app /app

RUN pip install --no-cache-dir -r /app/requirements.txt