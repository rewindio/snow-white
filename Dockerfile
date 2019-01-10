FROM python:2.7-alpine

RUN apk --no-cache add ca-certificates

RUN mkdir /src

COPY container-src/ /src/

WORKDIR /src

RUN pip install -r requirements.txt

ENTRYPOINT ["python","snow-white.py"]
CMD ["-h"]