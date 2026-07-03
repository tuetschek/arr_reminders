import os
import glob
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import yaml
import openreview

# Gmail API scopes
SCOPES = ['https://www.googleapis.com/auth/gmail.compose']

def get_gmail_service():
    """Authenticate and return Gmail API service."""
    creds = None

    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)

        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def get_missing_reviewers_emails(or_client, venue_id, ac_profile_id):
    # 1. Find all submissions where you are an assigned Area Chair.
    ac_groups = or_client.get_groups(prefix=f'{venue_id}', member=ac_profile_id)
    ac_groups = [g for g in ac_groups if g.id.endswith('/Area_Chairs') and 'Submission' in g.id]

    my_submission_numbers = [
        int(g.id.split('/Submission')[1].split('/')[0]) for g in ac_groups
    ]
    #    Each paper has its own AC group: venue_id/SubmissionN/Area_Chairs

    missing = []  # list of dicts: {submission, reviewer_id, name, email}

    for number in my_submission_numbers:

        print(f"Getting missing reviewers for submission {number}...")

        # 2. Get the reviewers assigned to this paper (real profile IDs)
        reviewer_group = or_client.get_group(f'{venue_id}/Submission{number}/Reviewers')
        assigned_reviewers = set(reviewer_group.members)

        # 3. Get submitted Official Reviews for this paper
        submission = or_client.get_notes(invitation=f'{venue_id}/-/Submission', number=number)[0]
        reviews = or_client.get_notes(
            forum=submission.id,
            invitation=f'{venue_id}/Submission{number}/-/Official_Review'
        )

        # Resolve each review's anon signature back to a real profile ID
        reviewers_who_submitted = set()
        for r in reviews:
            anon_id = r.signatures[0]  # e.g. .../Submission12345/Reviewer_XXXX
            anon_group = or_client.get_group(anon_id)
            reviewers_who_submitted.update(anon_group.members)  # real profile id(s)

        # 4. Anyone assigned but not in the "submitted" set is missing a review
        missing_reviewers = assigned_reviewers - reviewers_who_submitted

        for real_id in missing_reviewers:
            profile = openreview.tools.get_profiles(or_client, [real_id], with_publications=False, with_preferred_emails=(venue_id + '/-/Preferred_Emails'))[0]
            name = profile.content.get('names', [{}])[0].get('fullname', real_id)
            email = profile.content.get('preferredEmail')
            missing.append({
                'submission': number,
                'reviewer_id': real_id,
                'name': name,
                'email': email
            })

    return missing


def create_draft(service, sender, email, subject, body):
    """Create a Gmail draft."""
    message = MIMEMultipart()
    message['To'] = email
    message['Subject'] = subject
    message['From'] = sender

    msg_body = MIMEText(body, 'plain')
    message.attach(msg_body)

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')

    try:
        draft = service.users().drafts().create(
            userId='me',
            body={'message': {'raw': raw_message}}
        ).execute()
        return draft
    except HttpError as error:
        print(f'An error occurred: {error}')
        return None

def main():
    USERNAME = 'odusek@ufal.mff.cuni.cz'
    PASSWORD = 'gfNRYs6idtyhZ5a'
    AC_PROFILE_ID = '~Ondrej_Dusek1'        # your OpenReview profile id

    VENUE_ID = 'aclweb.org/ACL/ARR/2026/May'          # e.g. 'ICLR.cc/2026/Conference'
    SUBJECT = 'Your ARR May (EMNLP/AACL) Review for submission {paper_id} is due'
    BODY = '''Dear {author},

I'm your Area Chair for ARR May, and I'm still missing a review from you. Please check OpenReview and finish your review as soon as possible:

https://openreview.net/group?id={venue_id}/Reviewers

The deadline is today AoE, i.e., in about 30 minutes. Please either enter a delay notification on OpenReview or respond to this email and let me know your status in case you're not able to finish the review in the next few hours.

Thanks,
Ondrej Dusek
(odusek@ufal.mff.cuni.cz)'''

    print("Authenticating with Gmail...")
    service = get_gmail_service()
    print("Authentication successful!")

    print("Creating OR client...")
    or_client = openreview.api.OpenReviewClient(baseurl="https://api2.openreview.net", username=USERNAME, password=PASSWORD)

    print("Fetching OR papers...")
    missing_reviewers = get_missing_reviewers_emails(or_client, VENUE_ID, AC_PROFILE_ID)

    for reviewer in missing_reviewers:

        try:
            print(f"Creating email for {reviewer['name']}, submission {reviewer['submission']}")

            draft = create_draft(service,
                                 USERNAME,
                                 reviewer['email'],
                                 SUBJECT.format(paper_id=reviewer['submission']),
                                 BODY.format(venue_id=VENUE_ID, author=reviewer['name']))

            if draft:
                print(f"Draft created successfully! Draft ID: {draft['id']}")
            else:
                print(f"Failed to create draft for ID {reviewer['submission']}, reviewer {reviewer['name']}")

        except Exception as e:
            print(f"Error processing ID {reviewer['submission']}, reviewer {reviewer['name']}: {e}")

    print("\n=== Processing complete! ===")

if __name__ == '__main__':
    main()
