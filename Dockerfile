FROM python:3.12

ARG DD_GIT_REPOSITORY_URL
ARG DD_GIT_COMMIT_SHA
ENV DD_GIT_REPOSITORY_URL=${DD_GIT_REPOSITORY_URL}
ENV DD_GIT_COMMIT_SHA=${DD_GIT_COMMIT_SHA}

WORKDIR /usr/src/app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY main.py .
COPY config.py .
COPY models ./models

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
