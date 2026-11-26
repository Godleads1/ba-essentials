from django.core.management.base import BaseCommand, CommandError

from apps.core import client_cleanup

CONFIRM = "delete-all-client-data"


class Command(BaseCommand):
    help = (
        "Before launch only: remove every client business and client account (test data). Staff "
        "accounts, plans, the catalogue and website content stay. Without --confirm it only shows "
        "what would be removed."
    )

    def add_arguments(self, parser):
        parser.add_argument("--confirm", default="",
                            help=f"Type {CONFIRM} to actually delete. Leave it out for a dry run.")

    def handle(self, *args, **options):
        commit = options["confirm"] == CONFIRM
        if options["confirm"] and not commit:
            raise CommandError(f"To delete, pass exactly --confirm {CONFIRM}. Nothing was changed.")
        try:
            result = client_cleanup.run(commit=commit)
        except client_cleanup.Refused as refusal:
            raise CommandError(str(refusal)) from refusal
        heading = "REMOVED" if commit else "DRY RUN: nothing changed. This would remove"
        self.stdout.write(f"{heading}: {result['businesses']} businesses, {result['people']} client accounts, "
                          f"{result['files']} stored files.")
        for label, n in result["counts"].items():
            if n:
                self.stdout.write(f"  {label}: {n}")
        self.stdout.write(f"Staff accounts kept: {result['kept_staff']}.")
        if not commit:
            self.stdout.write(f"To delete, run again with: --confirm {CONFIRM}")
        else:
            self.stdout.write(self.style.SUCCESS("Done."))
