{#- A surrogate key: md5 of the natural key and version columns (domain model §3).
    md5 exists in Postgres and Redshift, so no dialect switch is needed. -#}
{% macro surrogate_key(columns) -%}
    md5(
        {%- for column in columns %}
        coalesce(cast({{ column }} as text), '<null>'){% if not loop.last %} || '|' ||{% endif %}
        {%- endfor %}
    )
{%- endmacro %}
