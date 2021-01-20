import click

from .submit_subjects import submit_subjects
from .check_status import check_status


CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS, no_args_is_help=True)
@click.version_option()
def cli():
    pass


cli.add_command(submit_subjects)
cli.add_command(check_status)
