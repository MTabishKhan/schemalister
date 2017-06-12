from __future__ import absolute_import
from celery import Celery
from django.conf import settings
import os
import datetime
import traceback

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'schemalister.settings')

app = Celery('tasks', broker=os.environ.get('REDISTOGO_URL', 'redis://localhost'))

from getschema.models import Schema, Object, Field, Debug, FieldUsage
from django.conf import settings
from suds.client import Client
from . import utils
import json	
import requests

@app.task
def get_objects_and_fields(schema): 

	instance_url = schema.instance_url
	org_id = schema.org_id
	access_token = schema.access_token

	# List of standard objects to include
	standard_objects = (
		'Account',
		'Activity',
		'Asset',
		'Campaign',
		'CampaignMember',
		'Case',
		'Contact',
		'ContentVersion',
		'Contract',
		'Event',
		'ForecastingAdjustment',
		'ForecastingQuota',
		'KnowledgeArticle',
		'Lead',
		'Opportunity',
		'OpportunityCompetitor',
		'OpportunityLineItem',
		'Order',
		'OrderItem',
		'Pricebook2',
		'PricebookEntry',
		'Product2',
		'Quote',
		'QuoteLineItem',
		'Solution',
		'Task',
		'User',
		'WorkOrder',
		'WorkOrderLineItem',
	)

	headers = {
		'Authorization': 'Bearer ' + access_token, 
		'content-type': 'application/json'
	}

	# Describe all sObjects
	all_objects = requests.get(
		instance_url + '/services/data/v' + str(settings.SALESFORCE_API_VERSION) + '.0/sobjects/', 
		headers=headers
	)

	try:

		if 'sobjects' in all_objects.json():

			for sObject in all_objects.json()['sobjects']:

				if sObject['name'] in standard_objects or sObject['name'].endswith('__c'):

					# Create object record
					new_object = Object()
					new_object.schema = schema
					new_object.api_name = sObject['name']
					new_object.label = sObject['label']
					new_object.save()

					# query for fields in the object
					all_fields = requests.get(instance_url + sObject['urls']['describe'], headers={'Authorization': 'Bearer ' + access_token, 'content-type': 'application/json'})

					# Loop through fields
					for field in all_fields.json()['fields']:

						# Create field
						new_field = Field()
						new_field.object = new_object
						new_field.api_name = field['name']
						new_field.label = field['label']

						if 'inlineHelpText' in field:
							new_field.help_text = field['inlineHelpText']

						# lookup field
						if field['type'] == 'reference':
							new_field.data_type = 'Lookup ('

							# Could be a list of reference objects
							for referenceObject in field['referenceTo']:
								new_field.data_type = new_field.data_type + referenceObject.title() + ', '

							# remove trailing comma and add closing bracket
							new_field.data_type = new_field.data_type[:-2]
							new_field.data_type = new_field.data_type + ')'

						# picklist values
						elif field['type'] == 'picklist' or field['type'] == 'multipicklist':
							new_field.data_type = field['type'].title() + ' ('

							# Add in picklist values
							for picklist in field['picklistValues']:
								new_field.data_type = new_field.data_type + picklist['label'] + ', '

							# remove trailing comma and add closing bracket
							new_field.data_type = new_field.data_type[:-2]
							new_field.data_type = new_field.data_type + ')'

						# Text
						elif field['type'] == 'string':
							new_field.data_type = 'Text (' + str(field['length']) + ')'

						# Int
						elif field['type'] == 'int':
							new_field.data_type = 'Number (' + str(field['digits']) + ', 0)'

						elif field['type'] == 'boolean':
							new_field.data_type = 'Checkbox'

						# everything else
						else:
							new_field.data_type = field['type'].title()

							# Change Double to Number
							if new_field.data_type == 'Double':
								new_field.data_type = 'Number'

							# If there is a length component, add to the field type
							if 'length' in field and int(field['length']) > 0:
								new_field.data_type += ' (' + str(field['length']) + ')'

							# If there is a precision element
							if 'precision' in field and int(field['precision']) > 0:

								# Determine the number of digits
								num_digits = int(field['precision']) - int(field['scale'])

								# Set the precision and scale against the field
								new_field.data_type += ' (' + str(num_digits) + ', ' + str(field['scale']) + ')'

						new_field.save()

			
			# If the user wants to see all the places the fields are used
			# run logic to query for other metadata
			if schema.include_field_usage:

				try:

					# Get all fields for the schema
					all_fields = Field.objects.filter(object__schema=schema)

					# Get a list of page layouts
					layout_urls = utils.get_urls_for_object(schema, 'Layout')

					# Get the metadatafor each laout
					for layout_url in layout_urls:

						# Get the metadata for the layout
						layout_result = requests.get(layout_url, headers=headers)

						# Convert to json object
						layout_json = layout_result.json()

						# Iterate over each field to determine if it's included in a layout
						for field in all_fields:

							layout_full_name = layout_json['FullName']
							layout_object_name = layout_full_name.split('-')[0]

							# Convert all layout columns to a string
							layout_fields_string = json.dumps(layout_json['Metadata']['layoutSections'])

							# If field object matches the layout object, and the field is in one of the columns
							if field.object.api_name == layout_object_name and field.api_name in layout_fields_string:

								field_usage = FieldUsage()
								field_usage.field = field
								field_usage.type = 'Page Layout'
								field_usage.name = layout_full_name
								field_usage.save()


					schema.status = 'Finished'

				except Exception as error:
					schema.status = 'Error'
					schema.error = traceback.format_exc()

			else:
				schema.status = 'Finished'

		else:

			schema.status = 'Error'
			schema.error = 'There was no objects returned from the query'

			debug = Debug()
			debug.debug = all_objects.text
			debug.save()

	except Exception as error:
		schema.status = 'Error'
		schema.error = traceback.format_exc()
	
	schema.finished_date = datetime.datetime.now()
	schema.save()

	return str(schema.id)