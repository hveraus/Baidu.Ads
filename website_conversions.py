"""Validate daily website aggregates allowed in the public dashboard."""
import datetime as dt
import json

FILENAME='website-conversions.json'
METRICS=('sessions','install_copy_sessions','email_click_sessions')
FIELDS=('date',*METRICS)
TINA_PAID_FILENAME='website-paid-email-clicks.json'
TINA_PAID_FIELDS=('date','paid_sessions','paid_email_click_sessions')
SSW_FILENAME='website-paid-conversions.json'
SSW_FIELDS=('date','paid_sessions','paid_contact_click_sessions')


def validate_rows(rows,fields=FIELDS):
 if not isinstance(rows,list):
  raise ValueError('Website conversion data must be a list of daily aggregates')
 dates=set()
 for row in rows:
  if not isinstance(row,dict) or set(row)!=set(fields):
   raise ValueError('Website conversion data contains unexpected fields')
  date=row['date']
  if not isinstance(date,str) or dt.date.fromisoformat(date).isoformat()!=date or date in dates:
   raise ValueError('Website conversion dates must be unique ISO dates')
  dates.add(date)
  sessions=row[fields[1]]
  if type(sessions) is not int or not 0<=sessions<=1_000_000_000:
   raise ValueError('Website sessions must be a non-negative integer')
  for metric in fields[2:]:
   value=row[metric]
   if value is not None and (type(value) is not int or not 0<=value<=sessions):
    raise ValueError('Converted sessions must be between zero and total sessions')
 return sorted(rows,key=lambda row:row['date'])


def load_rows(path,fields=FIELDS):
 return validate_rows(json.loads(path.read_text()),fields) if path.exists() else []
