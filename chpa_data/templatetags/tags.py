from django import template

register = template.Library()

@register.filter(name='percentage')
def percentage(value, decimal):
    try:
        format_str = '{0:.'+ str(decimal) + '%}'
        return format_str.format(value)
    except:
        return value