FROM python

LABEL org.opencontainers.image.source=https://github.com/volkovskiyda/telegram-dasovbot

RUN mkdir project
WORKDIR /project
COPY info.py main.py requirements.txt ./

RUN python -m pip install --upgrade pip
RUN pip install -U -r requirements.txt

CMD python main.py
