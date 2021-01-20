from collections import defaultdict
import datetime
import json
import logging
import os
import sys
import time
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError
import click

logging.basicConfig(format="%(message)s", level=logging.INFO)


def read_file(input_file):
    file_contents = []
    with open(input_file) as f:
        for line in f:
            file_contents.append(line.strip())

    return file_contents


def read_json(json_file):
    """ (file) -> dict
    Read in json_file, which is in json format, and
    output a dict with its contents
    """
    with open(json_file) as f:
        data = json.load(f)
    return data


def write_json(data, json_file):
    with open(json_file, "w") as data_file:
        json.dump(data, data_file, default=repr, sort_keys=True, indent=2)


def chunker(seq, size):
    """ https://stackoverflow.com/a/434328 """
    return (seq[pos : pos + size] for pos in range(0, len(seq), size))


def split_s3_path(image):
    parsed = urlparse(image)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


def get_status(job_ids):
    # set up batch client for all things that need a connection
    batch_client = boto3.client("batch")
    # describe_jobs only handles 100 jobs at a time
    job_descriptions = []
    for job in chunker(job_ids, 100):
        response = batch_client.describe_jobs(jobs=job)
        job_descriptions.extend(response["jobs"])

    # create a dict of different status, with a dict of {job: [info]} with that status
    status_dict = defaultdict(list)
    for job in job_descriptions:
        status = job["status"]

        try:
            exit_code = job["container"]["exitCode"]
        except KeyError:
            exit_code = ""

        try:
            log = job["container"]["logStreamName"]
        except KeyError:
            log = ""

        try:
            reason = job["container"]["reason"]
        except KeyError:
            try:
                reason = job["statusReason"]
            except KeyError:
                reason = ""

        environment = job["container"]["environment"]
        for i in environment:
            if i["name"] == "OUTPUT_PATH":
                output_path = i["value"]
                break

        status_dict[status].append(
            {
                job["jobId"]: {
                    "output_path": output_path,
                    "log": log,
                    "exit_code": exit_code,
                    "reason": reason,
                    "status": status,
                }
            }
        )

    return status_dict


def get_summary(status_dict, summary):
    summary_dict = {}
    for k, v in status_dict.items():
        summary_dict[k] = len(v)

    if summary:
        print("[{:%Y-%m-%d %H:%M:%S}] Status summary:".format(datetime.datetime.now()))
        for k, v in summary_dict.items():
            print(f"  {k}: {v}")
        print("---")

    return summary_dict


def poll_status(job_ids, summary, status_file):
    status_dict = get_status(job_ids)
    summary_dict = get_summary(status_dict, summary)
    write_json(status_dict, status_file)
    return status_dict, summary_dict


def still_processing(summary_dict):
    still_processing_values = [
        "SUBMITTED",
        "PENDING",
        "RUNNABLE",
        "STARTING",
        "RUNNING",
    ]
    for i in still_processing_values:
        if i in summary_dict:
            return True
    return False


def get_output_paths_from_status(status_file, output_status):
    status_dict = read_json(status_file)
    output_paths = []

    if output_status == "both":
        statuses_to_save = ["FAILED", "SUCCEEDED"]
    else:
        statuses_to_save = [output_status]

    for status in statuses_to_save:
        for subject in status_dict[status]:
            output_paths.append(dict(*subject.values())["output_path"])

    return output_paths


def get_output_paths_from_s3(output_bucket):
    ...


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 160}


@click.command(context_settings=CONTEXT_SETTINGS)
@click.version_option()
@click.option(
    "--submission_log",
    "-l",
    type=click.Path(),
    default=os.path.join(os.getcwd(), "submission.json"),
    help=(
        "File path of where to save CSV of job submission details "
        "Default is $PWD/submission.json."
    ),
)
@click.option(
    "--status_file",
    "-s",
    type=click.Path(),
    default=os.path.join(os.getcwd(), "status.json"),
    help=(
        "Status of submitted jobs, in JSON format. "
        "Will write to this file if used with --poll, "
        "otherwise this file is used to collect outputs (with the --get_* flags). "
        "Default is $PWD/status.json."
    ),
)
@click.option(
    "--poll",
    is_flag=True,
    default=False,
    help=(
        "Poll AWS Batch for information about submitted jobs. "
        "Will write to the value of --status_file/-s "
        "Default will not poll."
    ),
)
@click.option(
    "--continuous",
    is_flag=True,
    default=False,
    help=(
        "Poll AWS Batch until all jobs have status 'SUCCEEDED' or 'FAILED'. "
        "Has no effect unless --poll is also used. "
        "Default will not continuously poll."
    ),
)
@click.option(
    "--summary/--no_summary",
    default=True,
    help=(
        "Summarize status info of submitted jobs, by printing number of jobs in each state. "
        "Has no effect unless --poll is also used. "
        "Default is to produce a summary."
    ),
)
@click.option(
    "--output_list",
    "-f",
    default=None,
    help=(
        "File containing locations of THINQ/reTHINQ output, one AWS S3 path per line. "
        "Used in specifying which subjects to get data from with one or more of the --get-* flags. "
        "Not compatible with --poll (will only poll and ignore this option). "
        "Not compatible with --output_status "
        "(will only output results from subjects with the specified status). "
        "Cannot use with --output_path/-o or --output_bucket/-O"
    ),
)
@click.option(
    "--output_bucket",
    "-O",
    default=None,
    help=(
        "AWS S3 path pointing to the output of a group of THINQ/reTHINQ subjects. "
        "Used in specifying which subjects to get data from with one or more of the --get-* flags. "
        "Not compatible with --poll (will only poll and ignore this option). "
        "Not compatible with --output_status "
        "(will only output results from subjects with the specified status). "
        "Cannot use with --output_list/-f or ----output_path/-o"
    ),
)
@click.option(
    "--output_path",
    "-o",
    multiple=True,
    help=(
        "AWS S3 path for locations of THINQ/reTHINQ output. "
        "Can use multiple times "
        "(such as `-o s3://bucket/output_path -o s3://bucket/output_path2). "
        "Used in specifying which subjects to get data from with one or more of the --get-* flags. "
        "Not compatible with --poll (will only poll and ignore this option). "
        "Not compatible with --output_status "
        "(will only output results from subjects with the specified status). "
        "Cannot use with --output_list/-f or --output_bucket/-O"
    ),
)
@click.option(
    "--output_status",
    type=click.Choice(["SUCCEEDED", "FAILED", "both"]),
    help=(
        "Use output_paths from all subjects with the specified status. "
        "'both' will get outputs from 'SUCCEEDED' and 'FAILED' subjects. "
        "Requires a status_file (specifed with --status_file/-s). "
    ),
)
@click.option(
    "--save_location",
    default=None,
    help=(
        "Location of where to save outputs of --get-* commands. "
        "Not compatible with --poll (will only poll and ignore this option). "
    ),
)
@click.option(
    "--get_log",
    is_flag=True,
    default=False,
    help=(
        "Download log file from the specified subject(s). "
        "Not compatible with --get-all (if that is used, it will override this option."
        "Not compatible with --poll (will only poll and ignore this option). "
    ),
)
@click.option(
    "--get_report",
    is_flag=True,
    default=False,
    help=(
        "Download report pdf from the specified subject(s). "
        "Not compatible with --get-all (if that is used, it will override this option."
        "Not compatible with --poll (will only poll and ignore this option). "
    ),
)
@click.option(
    "--get_artifact",
    is_flag=True,
    default=False,
    help=(
        "Download the artifact.tar.gz from the specified subject(s). "
        "Not compatible with --get-all (if that is used, it will override this option."
        "Not compatible with --poll (will only poll and ignore this option). "
    ),
)
@click.option(
    "--get_subject_info",
    is_flag=True,
    default=False,
    help=(
        "Download the subject_info.json from the specified subject(s). "
        "Not compatible with --get-all (if that is used, it will override this option."
        "Not compatible with --poll (will only poll and ignore this option). "
    ),
)
@click.option(
    "--get_all",
    is_flag=True,
    default=False,
    help=(
        "Download the log file, report and artifact.tar.gz from the specified subject(s). "
        "Not compatible with other --get-* flags (those will be ignored)."
        "Not compatible with --poll (will only poll and ignore this option). "
    ),
)
@click.option(
    "--ignore_nonexistant",
    is_flag=True,
    default=False,
    help=(
        "If a file that should be be downloaded doesn't exist, "
        "ignore instead of exiting with an error. "
        "Not compatible with --poll (will only poll and ignore this option). "
    ),
)
def check_status(
    submission_log,
    status_file,
    poll,
    continuous,
    summary,
    output_list,
    output_bucket,
    output_path,
    output_status,
    save_location,
    get_log,
    get_report,
    get_artifact,
    get_subject_info,
    get_all,
    ignore_nonexistant,
):
    if poll:
        # get jobIds to use for polling Batch
        job_dict = read_json(submission_log)
        job_ids = list(job_dict.keys())
        # poll Batch for descriptions, where status etc can be extracted
        # write results to status_file
        status_dict, summary_dict = poll_status(job_ids, summary, status_file)

        # continuously poll until all jobs are completed)
        if continuous:
            time.sleep(60)
            while still_processing(summary_dict):
                status_dict, summary_dict = poll_status(job_ids, summary, status_file)
                time.sleep(60)

    else:
        if (
            not output_path
            and not output_list
            and not output_bucket
            and not output_status
        ):
            message = (
                "Must specify location of output, with one of:\n"
                "    --status_file/-s and --output_status\n"
                "    --output_list/-f\n"
                "    --output_path/-o\n"
                "    --output_bucket/-O\n"
            )
            sys.exit(message)
        if (
            (output_list and output_path)
            or (output_list and output_bucket)
            or (output_bucket and output_path)
        ):
            message = (
                "Cannot specify multiple locations of output! Use one of:\n"
                "    --output_list/-f\n"
                "    --output_path/-o\n"
                "    --output_bucket/-O\n"
            )
            sys.exit(message)

        if not save_location:
            message = "Specify where to save output using --save_location"
            sys.exit(message)

        if output_status:
            paths = get_output_paths_from_status(status_file, output_status)
        else:
            if output_list:
                paths = read_file(output_list)
                raise NotImplementedError(
                    "Using --output_list/-f is not currently implemented. Use a --status_file and specify a status with --output_status"
                )
            if output_path:
                paths = output_path
                raise NotImplementedError(
                    "Using --output_path/-o is not currently implemented. Use a --status_file and specify a status with --output_status"
                )
            if output_bucket:
                paths = get_output_paths_from_s3(output_bucket)
                raise NotImplementedError(
                    "Using --output_bucket/-O is not currently implemented. Use a --status_file and specify a status with --output_status"
                )

        files_to_download = []
        if get_all:
            files_to_download.extend(
                ["log_file", "report_file", "artifact_file", "subject_info_file"]
            )
        else:
            if get_log:
                files_to_download.append("log_file")
            if get_report:
                files_to_download.append("report_file")
            if get_artifact:
                files_to_download.append("artifact_file")
            if get_subject_info:
                files_to_download.append("subject_info_file")
        if not files_to_download:
            message = "Specify which files are wanted using one of the --get-* flags"
            sys.exit(message)

        filename_dict = {
            "log_file": ["rethinq.log", "thinq.log"],
            "report_file": [
                "rethinq-report.pdf",
                "thinq-report.pdf",
                "error_report.pdf",
            ],
            "artifact_file": ["artifact.tar.gz"],
            "subject_info_file": ["subject_info.json"],
        }

        # connect the s3 client and download files
        s3 = boto3.client("s3")
        nonexistant = []
        for path in paths:
            # setup save paths, creating a directory for group and subject
            subject_save_location = os.path.join(
                save_location, path.split("/")[-2], path.split("/")[-1]
            )
            os.makedirs(subject_save_location, exist_ok=True)
            for file_type in files_to_download:
                for filename in filename_dict[file_type]:
                    try:
                        s3_file_path = os.path.join(path, filename)
                        destination = os.path.join(subject_save_location, filename)
                        bucket, key = split_s3_path(s3_file_path)
                        s3.download_file(bucket, key, destination)
                        logging.log(
                            logging.INFO, f"Downloading {s3_file_path} to {destination}"
                        )
                        downloaded = True
                        break
                    except ClientError:
                        downloaded = False
                if not downloaded:
                    message = (
                        f"#@# Could not find the '{file_type}' in the location:'{path}'"
                    )
                    if ignore_nonexistant:
                        logging.log(logging.INFO, message)
                        nonexistant.append(path)
                    else:
                        sys.exit(message)
        if nonexistant:
            logging.log(logging.INFO, "The following paths are missing files:")
            for i in set(nonexistant):
                logging.log(logging.INFO, f"  {i}")
