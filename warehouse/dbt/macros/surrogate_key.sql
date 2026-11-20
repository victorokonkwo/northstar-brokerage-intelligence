{#- A surrogate key: md5 of the natural key and version columns (domain model §3).
    md5 gives the same digest on Postgres and Redshift (docs/sources.md); the
    cast uses string_type() because Redshift's `text` is only varchar(256). -#}
{% macro surrogate_key(columns) -%}
    md5(
        {%- for column in columns %}
        coalesce(cast({{ column }} as {{ string_type() }}), '<null>'){% if not loop.last %} || '|' ||{% endif %}
        {%- endfor %}
    )
{%- endmacro %}
