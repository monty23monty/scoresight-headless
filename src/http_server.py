import logging
import os
import socket
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import csv
import xml.etree.ElementTree as ET
from io import StringIO
from dotenv import load_dotenv

import uvicorn

from text_detection_target import TextDetectionTargetWithResult
from sc_logging import logger, file_handler
from resource_path import resource_path

load_dotenv(resource_path(".env"))

PORT = 18099
http_results = []
http_results_lock = threading.Lock()
server_instance: uvicorn.Server | None = None
server_thread: threading.Thread | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger = logging.getLogger("uvicorn.access")
    logger.addHandler(file_handler)
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=False,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

HTML_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scoreboard</title>
<style>
  body { font-family: Arial, sans-serif; text-align: center; }
  .scoreboard { display: flex; justify-content: normal; align-items: center; margin-top: 20px; }
  .team { width: 25%; }
  .info { background: #f0f0f0; padding: 20px; border-radius: 10px; }
  .team-name { font-size: 24px; margin-bottom: 10px; }
  .score { font-size: 48px; margin-bottom: 10px; }
  .timer { font-size: 78px; margin: 0 20px; }
  .fouls { font-size: 24px; }
</style>
</head>
<body>
<script>
  function updateScoreboard() {
    fetch('http://localhost:18099/json')
      .then(response => response.json())
      .then(data => {
        if (data.find(item => item.name === 'Home Score') !== undefined) {
          const homeScore = data.find(item => item.name === 'Home Score').text;
          document.querySelector('.team.home .score').textContent = homeScore;
        }

        if (data.find(item => item.name === 'Away Score') !== undefined) {
          const awayScore = data.find(item => item.name === 'Away Score').text;
          document.querySelector('.team.away .score').textContent = awayScore;
        }
        
        if (data.find(item => item.name === 'Home Fouls') !== undefined) {
          const homeFouls = data.find(item => item.name === 'Home Fouls').text;
          document.querySelector('.team.home .fouls').textContent = `Fouls: ${homeFouls}`;
        }
        
        if (data.find(item => item.name === 'Away Fouls') !== undefined) {
          const awayFouls = data.find(item => item.name === 'Away Fouls').text;
          document.querySelector('.team.away .fouls').textContent = `Fouls: ${awayFouls}`;
        }

        if (data.find(item => item.name === 'Time') !== undefined) {
          const timeItem = data.find(item => item.name === 'Time');
          if (timeItem.state === 'Success' || timeItem.state === 'SameNoChange') {
            const time = timeItem.text;
            document.querySelector('.timer').textContent = time;
          }
        }
      })
      .catch(error => console.error('Error:', error));
  }

  setInterval(updateScoreboard, 100);
</script>

<div class="scoreboard">
  <div class="team home">
    <div class="info">
      <div class="team-name">Home Team</div>
      <div class="score">0</div>
      <div class="fouls">Fouls: 0</div>
    </div>
  </div>
  <div class="timer">00:00</div>
  <div class="team away">
    <div class="info">
      <div class="team-name">Away Team</div>
      <div class="score">0</div>
      <div class="fouls">Fouls: 0</div>
    </div>
  </div>
</div>

</body>
</html>
"""


@app.get("/scoresight", response_class=HTMLResponse)
async def get_html():
    return HTMLResponse(content=HTML_PAGE)


# Example JSON response
@app.get("/json")
async def get_json(pivot=Query(None)):
    # check querystring for "?pivot" to pivot the data
    with http_results_lock:
        results = list(http_results)
    data = {}
    if pivot is not None:
        for result in results:
            if (
                result.result_state == TextDetectionTargetWithResult.ResultState.Success
                or result.result_state
                == TextDetectionTargetWithResult.ResultState.SameNoChange
            ):
                data[result.name] = result.result
    else:
        data = [result.to_dict() for result in results]

    return JSONResponse(content=data)


@app.get("/xml")
async def get_xml(pivot=Query(None)):
    with http_results_lock:
        results = list(http_results)
    root = ET.Element("data")
    if pivot is not None:
        data = {}
        for result in results:
            if (
                result.result_state == TextDetectionTargetWithResult.ResultState.Success
                or result.result_state
                == TextDetectionTargetWithResult.ResultState.SameNoChange
            ):
                data[result.name] = result.result
        for key in data:
            # transform key to camelCase
            key_xml = "".join([word.title() for word in key.split(" ")])
            ET.SubElement(root, key_xml).text = data[key]
    else:
        for targetWithResult in results:
            resultEl = ET.SubElement(root, "result")
            resultEl.set("name", targetWithResult.name)
            resultEl.set("result", targetWithResult.result)
            resultEl.set("result_state", targetWithResult.result_state.name)
            resultEl.set("x", str(targetWithResult.x()))
            resultEl.set("y", str(targetWithResult.y()))
            resultEl.set("width", str(targetWithResult.width()))
            resultEl.set("height", str(targetWithResult.height()))

    return Response(content=ET.tostring(root), media_type="text/xml")


@app.get("/csv")
async def get_csv():
    with http_results_lock:
        results = list(http_results)
    output = StringIO()
    csv_writer = csv.writer(output)
    csv_writer.writerow(["Name", "Text", "X", "Y", "Width", "Height"])
    for result in results:
        csv_writer.writerow(
            [
                result.name,
                result.result,
                result.x(),
                result.y(),
                result.width(),
                result.height(),
            ]
        )
    return Response(content=output.getvalue(), media_type="text/csv")


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def start_http_server():
    global server_thread

    def run_uvicorn():
        global server_instance
        if is_port_in_use(PORT):
            logger.error(f"Port {PORT} is already in use")
            return

        config = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=PORT,
            loop="asyncio",
            log_level="debug" if os.getenv("SCORESIGHT_DEBUG") else "info",
            log_config=None,
            lifespan="on",
        )
        server_instance = uvicorn.Server(config)

        logger.info(f"Server starting at port {PORT}")
        try:
            server_instance.run()
        except Exception as e:
            logger.error(f"Error running server: {e}")
        server_instance = None
        logger.info("Server thread stopped")

    # Start Uvicorn server in a separate thread
    server_thread = threading.Thread(
        target=run_uvicorn, name="scoresight-legacy-http", daemon=True
    )
    server_thread.start()


def stop_http_server():
    global server_thread
    logger.info("Stopping server...")
    if server_instance is not None:
        server_instance.should_exit = True
    if server_thread is not None and server_thread.is_alive():
        server_thread.join(timeout=5)
    server_thread = None


def update_http_server(results: list[TextDetectionTargetWithResult]):
    with http_results_lock:
        http_results[:] = results
