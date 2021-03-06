from celery import task
import csv
from metrics_manager.models import IncomingData, MetricSummary, Channel
from celery_app.metric_sender import fire
import iso8601
from django.db import IntegrityError, transaction
from django.db.models import Sum
import logging
logger = logging.getLogger(__name__)


@task()
@transaction.atomic
def ingest_csv(csv_data, channel, default_country_code):
    """ Expecting data in the following format:
    headers = {
        "mxit": ["Date", "UserID", "Nick", "Mxit Email", "Name & Surname", "Mobile",
            "Optional. email address - (Dont have an email address? Use your mxit address (mxitid@mxit.im)",
            "Country"],
        "eskimi": ["Date", "First name:", "Second name:", "Email:", "Mobile number:",
            "age", "city", "gender"],
        "binu": ["Date", "Country", "City", "SurveyUserId",
            "I agree that AIDS, TB and malaria are all preventable and treatable  yet together they still kill more than 2 million Africans each year. I agree that spending promises through clear and open health budgets need to be upheld so these deaths can be avoided.",
            "Please enter your full name.", "Account ID", "User Name", "Age", "Sex",
            "Relationship Status", "Education Level", "Employment Status", "Num Children"]
    }
    """

    if channel.name == "mxit":
        # Mxit has extra header
        csv_data.seek(0)
        next(csv_data)
        records = csv.DictReader(csv_data)
        mxit_opt_email = "Enter your email address (optional). Don't have an email address? Use your mxit address (mxitid@mxit.im)"
        for line in records:
            try:
                incoming_data = IncomingData()
                incoming_data.source_timestamp = iso8601.parse_date(
                    line["Date"])
                incoming_data.channel = channel
                incoming_data.channel_uid = line["UserID"][:254]
                if line[mxit_opt_email] is None:
                    incoming_data.email = line["Mxit Email"][:254]
                else:
                    incoming_data.email = line[mxit_opt_email][:254]
                if line["Enter your name"] is not None:
                    incoming_data.name = line["Enter your name"][:254]
                if line["Enter your mobile number"] is not None:
                    incoming_data.msisdn = line["Enter your mobile number"][:99]
                incoming_data.country_code = default_country_code
                incoming_data.save()
            except IntegrityError as e:
                incoming_data = None
                # crappy CSV data
                logger.error(e)
        # return sum_and_fire.delay(channel)  # send metrics
    elif channel.name == "eskimi":
        records = csv.DictReader(csv_data)
        for line in records:
            try:
                incoming_data = IncomingData()
                incoming_data.source_timestamp = iso8601.parse_date(
                    line["Date"])
                incoming_data.channel = channel
                incoming_data.channel_uid = line["Mobile number:"]
                incoming_data.email = line["u_email"]
                incoming_data.name = line["First name:"] + \
                    " " + line["Second name:"]
                incoming_data.msisdn = line["Mobile number:"]
                if "country" in line and line["country"] is not None:
                    incoming_data.country_code = line["country"]
                else:
                    incoming_data.country_code = default_country_code
                incoming_data.save()
            except IntegrityError as e:
                incoming_data = None
                # crappy CSV data
                logger.error(e)
        # return sum_and_fire.delay(channel)  # send metrics
    elif channel.name == "binu":
        records = csv.DictReader(csv_data)
        for line in records:
            try:
                incoming_data = IncomingData()
                incoming_data.source_timestamp = iso8601.parse_date(
                    line["Date"])
                incoming_data.channel = channel
                incoming_data.channel_uid = line["Account ID"]
                incoming_data.name = line["Please enter your full name."]
                incoming_data.age = line["Age"]
                if "Country" in line and line["Country"] is not None:
                    incoming_data.country_code = line["Country"]
                else:
                    incoming_data.country_code = default_country_code
                incoming_data.location = line["City"]
                if line["Sex"] == "M":
                    incoming_data.gender = "male"
                else:
                    incoming_data.gender = "female"
                incoming_data.save()
            except IntegrityError as e:
                incoming_data = None
                # crappy CSV data
                logger.error(e)
        # return sum_and_fire.delay(channel)  # send metrics


@task()
def sum_and_fire(channel):
    """ When a channel is updated a number of metrics needs sending to Vumi """
    response = {}
    metrics = MetricSummary.objects.filter(channel=channel).all()
    for metric in metrics:
        total = IncomingData.objects.filter(channel=channel).filter(
            country_code=metric.country_code).count()
        metric_name = "%s.%s.%s" % (
            str(metric.country_code), str(metric.channel.name), str(metric.metric))
        if total is not 0:
            response[metric_name] = fire(metric_name, total, "LAST")
        metric.total = total
        metric.save()
    
    return response


@task()
def extract_and_fire(channel):
    response = {}
    metrics = MetricSummary.objects.filter(channel=channel)
    for metric in metrics:
        metric_name = "%s.%s.%s" % (
                str(metric.country_code), str(metric.channel.name), str(metric.metric))
        response[metric_name] = fire(metric_name, metric.total, "LAST")  # send metrics
    return response

@task()
def extract_and_fire_all():
    response = {}
    channels = Channel.objects.all()
    for channel in channels:
        response[channel.name] = extract_and_fire.delay(channel)
    return response


@task()
def sum_and_fire_totals():
    response = {}
    total_za_supporters = MetricSummary.objects.filter(country_code='za').aggregate(total=Sum('total'))
    response["za"] = fire("za.supporter", total_za_supporters["total"], "LAST")  # send metrics
    total_ng_supporters = MetricSummary.objects.filter(country_code='ng').aggregate(total=Sum('total'))
    response["ng"] = fire("ng.supporter", total_ng_supporters["total"], "LAST")  # send metrics
    total_tz_supporters = MetricSummary.objects.filter(country_code='tz').aggregate(total=Sum('total'))
    response["tz"] = fire("tz.supporter", total_tz_supporters["total"], "LAST")  # send metrics
    total_global_supporters = MetricSummary.objects.filter(country_code='global').aggregate(total=Sum('total'))
    response["supporter"] = fire("supporter", total_global_supporters["total"], "LAST")  # send metrics
    return response


