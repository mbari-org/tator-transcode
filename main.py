import os
from logging.config import dictConfig
import logging
from typing import List, Union
from types import SimpleNamespace
from fastapi import FastAPI, Body
from fastapi.exceptions import RequestValidationError
from redis import Redis
from rq import Queue
from rq.job import Job as Qjob
from models.job import Job
from models.response import Response
from config import LogConfig
from urllib.parse import urlparse
from uuid import uuid1

dictConfig(LogConfig().dict())
logger = logging.getLogger("transcode")

app = FastAPI(
    title="Transcode",
    description="Simple transcode API",
    version="0.0.0",
)

@app.exception_handler(RequestValidationError)
def validation_exception_handler(request, exc):
    logger.error(f"Request validation error: {exc}")
    return Response(message="Validation error!", error=str(exc))


def _qjob_to_job(qjob):
    job = vars(qjob.args[0])
    job.pop("work_dir", None)
    job.pop("path", None)
    job.pop("cleanup", None)
    job.pop("extension", None)
    job.pop("hwaccel", None)
    job.pop("force_fps", -1)
    status = qjob.get_status(refresh=True)
    if status in ["queued", "deferred", "scheduled"]:
        job["status"] = "pending"
    if status == "started":
        job["status"] = "running"
    if status in ["canceled", "stopped"]:
        job["status"] = "canceled"
    if status == "finished":
        job["status"] = "succeeded"
    if status == "failed":
        job["status"] = status
    if qjob.id is not None:
        job["id"] = qjob.id
    if qjob.enqueued_at is not None:
        job["start_time"] = qjob.enqueued_at.isoformat()
    if qjob.ended_at is not None:
        job["stop_time"] = qjob.ended_at.isoformat()
    return Job(**job)


def _gid_key(gid):
    return f"transcode_gid_{gid}"


def _project_key(project):
    return f"transcode_project_{project}"


def _use_internal_host(url):
    """Checks if the download url contains localhost, if so
    replaces external host with minio host.
    """
    hostname = urlparse(url).hostname
    is_localhost = hostname in ["localhost", "127.0.0.1"]
    if is_localhost:
        external_host = os.getenv("DEFAULT_LIVE_EXTERNAL_HOST")
        minio_host = os.getenv("DEFAULT_LIVE_ENDPOINT_URL")
        url = url.replace(external_host, minio_host)
    return url


def get_queue():
    rds = Redis(host=os.getenv("REDIS_HOST", ""))
    queue = Queue("transcodes", connection=rds)
    return rds, queue


def append_value(rds, key, value):
    value_list = rds.get(key)
    if value_list is None:
        rds.set(key, value)
    else:
        rds.set(key, value_list.decode("utf-8") + f",{value}")


def remove_value(rds, key, value):
    value_list = rds.get(key)
    if value_list is not None:
        value_list = value_list.decode("utf-8").split(",")
        if value in value_list:
            value_list.remove(value)
        rds.set(key, ",".join(value_list))


def get_list(rds, key):
    value_list = rds.get(key)
    if value_list is None:
        value_list = []
    else:
        value_list = value_list.decode("utf-8").split(",")
    return value_list


@app.delete(
    "/jobs",
    responses={
        200: {"model": Response, "description": "Successful deletion of jobs."},
        400: {"description": "Error deleting the transcode jobs."},
    },
    tags=["Transcode"],
    summary="Deletes a list of running transcodes.",
    response_model_by_alias=True,
)
def jobs_delete(
    uid_list: List[str] = Body(default=None),
    gid: Union[str, None] = None,
    project: Union[int, None] = None,
) -> Response:
    rds, _ = get_queue()
    if gid is not None:
        uid_list = get_list(rds, _gid_key(gid))
    elif project is not None:
        uid_list = get_list(rds, _project_key(project))
    elif uid_list is None:
        raise Exception("At least one parameter specifying jobs must be provided!")
    qjob_list = Qjob.fetch_many(uid_list, connection=rds)
    for qjob in qjob_list:
        qjob.cancel()
        job = qjob.args[0]
        remove_value(rds, _gid_key(job.gid), job.uid)
        remove_value(rds, _project_key(job.project), job.uid)
    return Response(message=f"Successfully canceled {len(qjob_list)} jobs!")


@app.post(
    "/jobs",
    responses={
        201: {"model": List[Job], "description": "List of created jobs."},
        400: {"description": "Error creating the transcode jobs."},
    },
    tags=["Transcode"],
    summary="Create one or more transcode jobs.",
    response_model_by_alias=True,
)
def jobs_post(job_list: List[Job]) -> List[Job]:
    try:
        rds, queue = get_queue()
        qjob_list = []
        for job in job_list:
            if job.uid is None:
                job.uid = str(uuid1())
            if job.gid is None:
                job.gid = str(uuid1())
            if job.url is not None:
                job.url = _use_internal_host(job.url)
            append_value(rds, _gid_key(job.gid), job.uid)
            append_value(rds, _project_key(job.project), job.uid)
            args = {
                **job.dict(),
                "path": None,
                "work_dir": "/tmp",
                "cleanup": False,
                "extension": None,
                "hwaccel": False,
                "force_fps": -1,  # TODO: could be exposed to REST
                "inhibit_upload": False,
            }
            args = SimpleNamespace(**args)
            qjob_list.append(
                queue.enqueue(
                    "tator.transcode.__main__.transcode_main",
                    args,
                    job_id=job.uid,
                    job_timeout=3600 * 96,
                )
            )
        return [_qjob_to_job(job) for job in qjob_list]
    except Exception as e:
        logger.error(f"Error creating transcode jobs: {e}")
        raise e


@app.put(
    "/jobs",
    responses={
        200: {"model": List[Job], "description": "List of running jobs."},
        400: {"description": "Error retrieving the transcode jobs."},
    },
    tags=["Transcode"],
    summary="Returns a list of running transcodes.",
    response_model_by_alias=True,
)
def jobs_put(
    uid_list: List[str] = Body(default=None),
    gid: Union[str, None] = None,
    project: Union[int, None] = None,
) -> List[Job]:
    rds, _ = get_queue()
    if gid is not None:
        uid_list = get_list(rds, _gid_key(gid))
    elif project is not None:
        uid_list = get_list(rds, _project_key(project))
    elif uid_list is None:
        raise Exception("At least one parameter specifying jobs must be provided!")
    qjob_list = Qjob.fetch_many(uid_list, connection=rds)
    return [_qjob_to_job(job) for job in qjob_list if job is not None]
