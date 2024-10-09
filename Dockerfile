FROM python

RUN mkdir project
WORKDIR /project
VOLUME /project/config
VOLUME /project/videos
COPY info.py main.py utils.py /project/

RUN python -m pip install --upgrade pip
RUN pip install -U python-dotenv python-telegram-bot yt-dlp

CMD python main.py
