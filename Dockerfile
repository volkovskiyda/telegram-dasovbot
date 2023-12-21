FROM python:alpine

RUN mkdir project
WORKDIR /project
COPY info.py main.py requirements.txt /project/

RUN python -m pip install --upgrade pip
RUN pip install -U -r requirements.txt

CMD python main.py
