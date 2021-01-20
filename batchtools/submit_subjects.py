import datetime
import json
import logging
import os
import sys
import time
from urllib.parse import urlparse

import boto3
import click
import numpy as np
import pandas as pd

logging.basicConfig(
    format="[%(asctime)s] %(message)s", level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S"
)
timeout_seconds = 10800
SEED = 1234


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
    with open(json_file) as data_file:
        data = json.load(data_file)
    return data


def get_envvars(environment_variable_list):
    if environment_variable_list:
        additional_vars = []
        for envvar in environment_variable_list:
            split_point = envvar.index("=")
            k = envvar[:split_point]
            v = envvar[split_point + 1 :]
            d = {"name": k, "value": v}
            additional_vars.append(d)
    else:
        additional_vars = []

    return additional_vars


def get_images_from_s3_dataset(input_bucket, upload_metadata, bids):
    # setup boto stuff so it only needs to be called once
    if bids:
        s3_client = boto3.client("s3")
        bucket, base_key = split_s3_path(input_bucket)
    if upload_metadata:
        s3_resource = boto3.resource("s3")

    # Using the CMeDS-style demographics.tsv to ensure all required fields are available
    # future release may use participants.tsv directly with the bids option
    demographics_file = os.path.join(input_bucket, "demographics.tsv")
    demographics = pd.read_csv(demographics_file, sep="\t")

    images = []
    for _, row in demographics.iterrows():
        # a `file_type` column is required, which contains either dicom or nifti
        file_type = row["file_type"]
        if bids:
            subject_id = f"sub-{row['subject_id']}"
            key = os.path.join(base_key, subject_id)
            objs = s3_client.list_objects(Bucket=bucket, Prefix=key)
            # NOTE: this block gets around a missing image in bids, may be hacky for now
            try:
                t1w_images = [
                    obj["Key"] for obj in objs["Contents"] if "T1w.nii.gz" in obj["Key"]
                ]
                # #@# NOTE: choosing random image to use for now
                image = f"s3://{bucket}/{select_random_image(t1w_images)}"
            except KeyError:
                logging.log(logging.INFO, f"#@# {key}")
                continue
        else:
            subject_id = row["subject_id"]
            if file_type == "nifti":
                image = os.path.join(input_bucket, subject_id, f"{subject_id}.nii.gz")
            elif file_type == "dicom":
                image = os.path.join(input_bucket, subject_id, "dicom")
            else:
                raise ValueError("file_type must be either dicom or nifti")
        images.append(image)

        if upload_metadata:
            if (
                file_type == "dicom"
            ):  # we want the metadata to be named after `subject_id`, not 'dicom'
                image = os.path.join(os.path.dirname(image), subject_id)
            upload_metadata_json(image, row, s3_resource, bids, false_dates=True)

    return images


def select_random_image(image_list):
    np.random.seed(SEED)
    return np.random.choice(image_list)


def upload_metadata_json(image, row, s3_resource, bids, false_dates):
    metadata = create_metadata_json(row, false_dates)
    bucket, key = split_s3_path(image)
    content = json.dumps(metadata, indent=2, sort_keys=True)

    if bids:
        path, image = os.path.split(key)
        # subject name is the first part of the filename in BIDS, removing the `sub-` part
        subname = image.split("_")[0].lstrip("sub-")
        metadata_path = os.path.join(path, f"{subname}.json")
    else:
        metadata_path = f"{key.strip('nii.gz')}.json"

    logging.log(
        logging.INFO,
        f"#@# Uploading metadata to s3://{os.path.join(bucket, metadata_path)}:\n{metadata}",
    )
    return s3_resource.Object(bucket, metadata_path).put(Body=content)


def split_s3_path(image):
    parsed = urlparse(image)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


def create_metadata_json(row, false_dates=True):
    # #@# note-- false dates are used for now, if not specified
    # in demographics, dates should be in %Y%m%d format (or standardized in someway)
    # will use a made up date or put Unknown in for now, if not present. doesn't force format
    fake_date = datetime.date(1970, 1, 1)
    fake_scandate = False
    try:
        scan_date = (
            f"{datetime.datetime.strptime(str(row['scan_date']), '%Y%m%d'):%Y%m%d}"
        )
    except KeyError:
        if false_dates:
            scan_date = f"{fake_date:%Y%m%d}"
        else:
            scan_date = "Unknown"
        fake_scandate = True

    try:
        birth_date = f"{datetime.datetime.strptime(str(row['dob']), '%Y%m%d'):%Y%m%d}"
    except KeyError:
        if fake_scandate:
            if false_dates:
                birth_date = (
                    f"{fake_date - datetime.timedelta(days=365.25*row['age']):%Y%m%d}"
                )
            else:
                birth_date == "Unknown"
        else:
            birth_date = f"{datetime.datetime.strptime(scan_date, '%Y%m%d') - datetime.timedelta(days=365.25*row['age']):%Y%m%d}"

    scan_details = {
        "Patient's Name": row["subject_id"],
        "Patient ID": row["source"],
        "Patient's Sex": row["sex"],
        "Patient's Birth Date": str(birth_date),
        "Patient's Age": int(row["age"]),
        "Acquisition Date": str(scan_date),
        "Manufacturer": row["manufacturer"],
        "Magnetic Field Strength": float(row["field_strength"]),
        "Diagnosis": row["diagnosis"],
    }
    subject_metadata = {"metadata": {"scan_details": {}}}
    subject_metadata["metadata"]["scan_details"] = scan_details

    return subject_metadata


def create_submission_details(
    image, output_bucket, license, timeout, additional_vars, bids
):
    """
    Subject image is found from image name, using schema described in the study_dir readme
    """
    submission_details = get_parsed_s3_image(image, bids)

    submission_details["name"] = "--".join(
        [submission_details["group"], submission_details["subject"]]
    )
    submission_details["output_path"] = os.path.join(
        output_bucket, submission_details["group"], submission_details["subject"]
    )
    # #@# for now, hardcoding product_id/license_key within the submission, as opposed to the job_definition previously
    # #@# eventually, this needs to be changed to real values (and passed without exposing values)
    submission_details["environment_overrides"] = {
        "environment": [
            {"name": "INPUT_IMAGE", "value": submission_details["image"]},
            {"name": "CMET_TIMEOUT", "value": str(timeout)},
            {"name": "OUTPUT_PATH", "value": submission_details["output_path"]},
            *additional_vars,
            *license,
        ]
    }

    if submission_details["mode"] == "nifti":
        # makes assumption that sidecar json exists, which is required for CMet dataset structure
        if bids:
            json_path, _ = os.path.split(submission_details["image"])
            subject_json = os.path.join(
                json_path, f"{submission_details['subject']}.json"
            )
            submission_details["json"] = subject_json
        else:
            submission_details["json"] = f"{image.strip('nii.gz')}.json"
        submission_details["environment_overrides"]["environment"].append(
            {"name": "SUBJECT_METADATA", "value": submission_details["json"]}
        )

    return submission_details


def get_parsed_s3_image(s3_image_path, bids):
    bucket, path = split_s3_path(s3_image_path)
    split_path = path.split("/")
    parsed_dict = dict()
    if len(split_path) < 3:
        # this happens if the s3_image_path is not s3://bucket/dataset/subject/subject_image (or longer)
        message = "The provided s3_image_path is too short! Doesn't contain enough parts to determine subject's name/group."
        raise TypeError(message)

    if path.endswith("nii.gz"):
        parsed_dict["mode"] = "nifti"
    else:  # assuming if it is not a .nii.gz file, it is a directory of dicoms
        parsed_dict["mode"] = "dicom"

    parsed_dict["image"] = s3_image_path
    parsed_dict["bucket"] = bucket
    parsed_dict["key"] = path
    parsed_dict["image_basename"] = split_path[
        -1
    ]  # the last part of the s3_image path (the *.nii.gz file or dicom dir)

    if bids:
        split_image_basename = parsed_dict["image_basename"].split("_")[
            0
        ]  # in BIDS, the subject name is the first part of the image_basename
        parsed_dict["subject"] = split_image_basename.strip(
            "sub-"
        )  # remove the 'sub-' part (which is part of the BIDS spec)
        parsed_dict["group"] = find_bids_group(split_path)

    else:
        parsed_dict["subject"] = split_path[
            -2
        ]  # 2nd-to-last part of s3_image_path (which is the subject's name in the CMet dataset structure)
        parsed_dict["group"] = split_path[
            -3
        ]  # the 3rd-to-last part of s3_image_path (which is the directory containing subjects in the CMet dataset structure)

    return parsed_dict


def find_bids_group(split_path):
    group = ""
    for part in split_path:
        if (
            "sub-" in part
        ):  # this will first occur at 1 level deeper than the group, according to the BIDS spec
            return group
        else:
            group = part  # make this part the candidate for group name
    raise ValueError("Cannot determine group name!")


def submit_job(job, job_definition, queue, client, log=True):
    response = client.submit_job(
        jobName=job["name"],
        jobQueue=queue,
        jobDefinition=job_definition,
        containerOverrides=job["environment_overrides"],
    )

    if log:
        logging.log(logging.INFO, "#@# jobName: {}".format(job["name"]))
        logging.log(logging.INFO, response)

    return response


def save_submission_details(submitted_jobs, output_file, queue, job_definition):
    """ (dict(dict), str, str, str) -> None
    Write out a JSON file of information about Batch submissions to output_file,
    with details taken from submitted_jobs

    Includes the following items:
    jobId- the AWS generated ID: response['jobId']. also, this is the Key for each submitted
           job's JSON object
    jobName- the user-generated job name: response['jobName']
    date- time of submission: response['ResponseMetadata']['HTTPHeaders']['date']
          date is in the format of: '%a,%d %b %Y %H:%M:%S %Z'
    output_path- the location where processing output is saved
    subject_id- name of subject being processed
    image- image being processed
    group- group that the subject belongs to
    queue- queue ARN the job was submitted to
    jobDefinition- job defintion that was used for processing
    """
    submission_details = dict()

    for job_id, job in submitted_jobs.items():
        output_dict = dict()
        output_dict["jobId"] = job["jobId"]
        output_dict["jobName"] = job["jobName"]
        output_dict["date"] = job["ResponseMetadata"]["HTTPHeaders"]["date"]
        output_dict["output_path"] = job["output_path"]
        output_dict["subject_id"] = job["subject"]
        output_dict["image"] = job["image"]
        output_dict["group"] = job["group"]
        output_dict["queue"] = queue
        output_dict["jobDefinition"] = job_definition
        submission_details[job_id] = output_dict

    with open(output_file, "w") as f:
        json.dump(submission_details, f, sort_keys=True, indent=2)


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 160}


@click.command(context_settings=CONTEXT_SETTINGS)
@click.version_option()
@click.option(
    "--queue", "-q", help="AWS Batch job queue to submit to. Must be a valid ARN."
)
# @click.option(
#     "--container_tag",
#     "-t",
#     help=(
#         "Tag and URL of the container to be used within the batch job. "
#         "This builds a job definition on the fly (if one doesn't exist). "
#         "If the images to be processed are a mix of dicom and nifti, "
#         "then this MUST be used. "
#         "An example would be:\n"
#         "  - nifti: 123456789012.abc.ecr.us-east-1.amazonaws.com/my-company/rethinq/rethinq-test:1.0.0-rc.14\n"
#         "Cannot be used with: --job_definition/-j"
#     ),
# )
@click.option(
    "--job_definition",
    "-j",
    help=(
        "Job definition to run on AWS Batch. Must be a valid ARN. "
        # "Cannot be used with --container_tag/-t"
    ),
)
@click.option(
    "--image_list",
    "-f",
    default=None,
    help=(
        "File containing images to process, one AWS S3 path per line. "
        "Cannot use with --image/-i or --input_bucket/-I"
    ),
)
@click.option(
    "--input_bucket",
    "-I",
    default=None,
    help=(
        "Dataset to process, stored on S3 in CMet dataset structure format (CMeDS). "
        "This is an S3 directory, with a top-level demographics.tsv file "
        "containing all subjects that should be processed. "
        "Cannot use with --image_list/-f or --image/-i"
    ),
)
@click.option(
    "--image",
    "-i",
    multiple=True,
    help=(
        "AWS S3 path for image to process "
        "Can use multiple times "
        "(such as `-i s3://bucket/image -i s3://bucket/image2). "
        "Cannot use with --image_list/-f or --input_bucket/-I"
    ),
)
@click.option("--output_bucket", "-o", help=("AWS S3 bucket pathto write to. "))
@click.option(
    "--timeout",
    type=int,
    default=timeout_seconds,
    help=f"CMet timeout time in seconds. Default is {timeout_seconds} ({timeout_seconds/3600:.1f} hours).",
)
@click.option(
    "--save_details/--no_save_details",
    default=True,
    help="Save CSV of job submission details. Default will save details.",
)
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
    "--upload_metadata",
    is_flag=True,
    default=False,
    help=(
        "Upload metadata_json before submission. "
        "Can only be used with --input_bucket/-I. "
        "Default will not upload metadata"
    ),
)
@click.option(
    "--upload_only",
    is_flag=True,
    default=False,
    help=(
        "Only upload subject_metadata, and do not process any data. "
        "Can only be used with --upload_metadata. "
        "Default will also submit data for processing"
    ),
)
@click.option(
    "--check/--no_check",
    default=True,
    help="Checks whether image exists before submitting. Default will check image exists.",
)
@click.option(
    "--skip",
    is_flag=True,
    default=False,
    help=(
        "Skip images that don't exist, and submit remaining existing images. "
        "Can only be used with --check. "
        "Default will cause an exit if images don't exist"
    ),
)
@click.option(
    "--bids",
    is_flag=True,
    default=False,
    help=(
        "Input data is in BIDS format, as opposed to CMet dataset structure (CMeDS). "
        "Can only be used with --input_bucket/-I. Default is CMeDS"
    ),
)
@click.option(
    "--stagger",
    is_flag=True,
    default=False,
    help=(
        "Staggers submission of images so that AWS `toomanyrequests` errors don't occur. "
        "This submits 50 images, waits 5 minutes for them to begin processing, "
        "then submits additional images with a 2 second pause between each. "
        "Only does something if there are greater than 200 images to process. "
        "Default will not stagger (and submit all images with no waiting)"
    ),
)
# @click.option(
#     "--license_strict",
#     is_flag=True,
#     default=False,
#     help=(
#         "Uses a normal user license instead of a dev license. "
#         "This will cause submitted images to fail if certain metadata is not provided. "
#         "Note that most nifti input images will fail if this is used."
#         "Default will use a dev license (not failing out on thse checks)."
#     ),
# )
@click.option(
    "--license_file",
    "-L",
    help=(
        "Use a user-provided license. "
        "This must be a JSON file in the format of:\n"
        '[{"name": "CMET_RETHINQ_PRODUCT_ID","value":\n"cmet_rethinq_product_id_value"},\n"name": "CMET_RETHINQ_LICENSE_KEY","value":\n"cmet_rethinq_license_key_value"}]\n'
    ),
)
# @click.option(
#     "--product_type",
#     type=click.Choice(["rethinq", "thinq"]),
#     default="rethinq",
#     help=(
#         "Product type to use (either thinq or rethinq. "
#         "Note that thinq cannot except nifti inputs. "
#         "Default is rethinq"
#     ),
# )
@click.option(
    "--environment_variable",
    "-e",
    multiple=True,
    help=(
        "Additional environment variable(s) to pass to the container. "
        "NOTE: must use the syntax `KEY=VALUE` (no space in between)."
    ),
)
def submit_subjects(
    queue,
    # container_tag,  #@# setting up job definition by container is a bit of a hassle
    job_definition,
    image_list,
    input_bucket,
    image,
    output_bucket,
    timeout,
    save_details,
    submission_log,
    upload_metadata,
    upload_only,
    check,
    skip,
    bids,
    stagger,
    # license_strict,
    license_file,
    # product_type, #@# this will only work with rethinq
    environment_variable,
):
    # make sure only one way to specify input images is used
    if not image and not image_list and not input_bucket:
        message = (
            "Must specify input, with one of:\n"
            "    --image_list/-f\n"
            "    --image/-i\n"
            "    --input_bucket/-I\n"
        )
        sys.exit(message)
    if (
        (image_list and image)
        or (image_list and input_bucket)
        or (input_bucket and image)
    ):
        message = (
            "Cannot specify multiple input opions! Use one of:\n"
            "    --image_list/-f\n"
            "    --image/-i\n"
            "    --input_bucket/-I\n"
        )
        sys.exit(message)
    if not output_bucket:
        message = "Output bucket required!"
        sys.exit(message)
    if upload_metadata and not input_bucket:
        message = (
            "Can only specify --upload_metadata when giving a s3 dataset to process!"
        )
        sys.exit(message)
    if bids and not input_bucket:
        message = "Can only specify --bids when giving a s3 dataset to process!"
        sys.exit(message)

    # `upload_only` can only be done if `upload_metadata` is also selected
    if upload_only and not upload_metadata:
        message = "Can only do --upload_only when --upload_metadata is also selected!"
        sys.exit(message)

    # find images to process, and exit if there aren't any
    images_to_process = []

    if image_list:
        images_to_process.extend(read_file(image_list))
    if image:
        images_to_process.extend(image)
    if input_bucket:
        images_to_process.extend(
            get_images_from_s3_dataset(input_bucket, upload_metadata, bids)
        )
    if not images_to_process:
        message = "No images to process!"
        sys.exit(message)

    # only do checks and submissions if not upload_only
    if not upload_only:
        # license info
        if not license_file:
            message = "License is required!"
            sys.exit(message)
        license = read_json(license_file)

        if not job_definition:
            message = "job definition must be given! Use --job_definition/-j to specify"
            sys.exit(message)
        # make sure we check for image existence if we use skip
        if not check and skip:
            message = "Must check for image existence to be able to skip those that don't exist"
            sys.exit(message)
        # check that images exist
        nonexistent = []
        if check:
            s3 = boto3.client("s3")
            for img in images_to_process:
                bucket, key = split_s3_path(img)
                objs = s3.list_objects(Bucket=bucket, Prefix=key)
                if "Contents" not in objs:
                    nonexistent.append(img)
            if nonexistent:
                imgs = "\n".join(nonexistent)
                message = f"The following images do not exist:\n{imgs}"
                logging.log(logging.INFO, message)
                if skip:
                    logging.log(
                        logging.INFO,
                        "Nonexistent images were skipped, continuing to submit existing images...",
                    )
                if not skip:
                    message = (
                        "Exiting without submitting images.\n"
                        "Confirm the correct images are given, or run with --skip to submit all existing images.\n"
                        "Alternatively, run with the --no-check option to attempt to submit all images without checking for existence."
                    )
                    sys.exit(message)

        # start a boto3 session, check queue exists, submit the jobs, and optionally save the details
        batch_client = boto3.client("batch")
        queue_response = batch_client.describe_job_queues()
        all_queues = [queue["jobQueueArn"] for queue in queue_response["jobQueues"]]
        if queue not in all_queues:
            message = f"Queue '{queue}' does not exist!)"
            sys.exit(message)

        submitted_jobs = dict()
        count = 0
        for img in images_to_process:
            if (img in nonexistent) and skip:
                continue

            additional_vars = get_envvars(environment_variable)
            job = create_submission_details(
                img, output_bucket, license, timeout, additional_vars, bids
            )

            if stagger and len(images_to_process) > 200:
                if count == 50:
                    message = f"#@# Waiting 5 minutes for AWS to catch up..."
                    logging.log(logging.INFO, message)
                    time.sleep(300)

                if count > 50:
                    time.sleep(2)
            job_submission_response = submit_job(
                job, job_definition, queue, batch_client
            )

            job_key = job_submission_response["jobId"]
            submitted_jobs[job_key] = {**job, **job_submission_response}

            count += 1
            message = f"#@# Submitted job {count} out of {len(images_to_process)}"
            logging.log(logging.INFO, message)

        if save_details:
            save_submission_details(
                submitted_jobs, submission_log, queue, job_definition
            )
