FROM python:3.8-slim
ENV UWSGI_CHEAPER 0
WORKDIR /app

COPY ./app .

RUN pip install -r requirements.txt

ENTRYPOINT [ "python", "./smpp_worker.py" ]
