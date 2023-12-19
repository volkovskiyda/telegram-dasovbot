FROM python:alpine

RUN mkdir project
WORKDIR /project
COPY info.py main.py requirements.txt /project/

RUN python -m pip install --upgrade pip
RUN python -m venv venv
RUN source venv/bin/activate
RUN pip install -U -r requirements.txt

CMD python main.py
