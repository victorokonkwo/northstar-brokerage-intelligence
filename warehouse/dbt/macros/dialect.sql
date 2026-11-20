{#- Portable SQL for the two warehouses: PostgreSQL (local and CI) and Amazon
    Redshift (cloud, ADR 005). Each macro renders the Postgres form unchanged on
    Postgres. The Redshift facts behind them are in docs/sources.md. -#}

{#- The SQL type for strings. Redshift turns `text` into varchar(256) and
    refuses longer values, so it gets the same width as the Python-managed
    tables (northstar.infrastructure.dialect.REDSHIFT_TEXT). -#}
{% macro string_type() -%}
    {{ 'varchar(4096)' if target.type == 'redshift' else 'text' }}
{%- endmacro %}

{#- The text at a path inside a JSON column, or null when the path is absent:
    json_text('payload', 'policy_versions', 'calendar').
    Postgres: payload -> 'policy_versions' ->> 'calendar'.
    Redshift (SUPER): payload."policy_versions"."calendar"::varchar(4096). -#}
{% macro json_text(column) -%}
    {%- set path = varargs -%}
    {%- if path | length == 0 -%}
        {{ exceptions.raise_compiler_error("json_text needs at least one key") }}
    {%- endif -%}
    {%- if target.type == 'redshift' -%}
        {{ column }}{% for key in path %}."{{ key }}"{% endfor %}::{{ string_type() }}
    {%- else -%}
        {{ column }}{% for key in path[:-1] %} -> '{{ key }}'{% endfor %} ->> '{{ path[-1] }}'
    {%- endif -%}
{%- endmacro %}

{#- The JSON value (not text) at a path: jsonb on Postgres, SUPER on Redshift. -#}
{% macro json_value(column) -%}
    {%- set path = varargs -%}
    {%- if target.type == 'redshift' -%}
        {{ column }}{% for key in path %}."{{ key }}"{% endfor %}
    {%- else -%}
        {{ column }}{% for key in path %} -> '{{ key }}'{% endfor %}
    {%- endif -%}
{%- endmacro %}

{#- One row per element of a JSON array of strings, with the row's key:
    select <key>, <element> as <name> from <relation> unnested over <array>.
    Postgres uses jsonb_array_elements_text; Redshift navigates the SUPER array
    in the FROM clause (PartiQL). -#}
{% macro json_array_texts(relation, key, array_column, name) -%}
    {%- if target.type == 'redshift' -%}
        select s.{{ key }}, e::{{ string_type() }} as {{ name }}
        from {{ relation }} as s, s.{{ array_column }} as e
    {%- else -%}
        select s.{{ key }}, jsonb_array_elements_text(s.{{ array_column }}) as {{ name }}
        from {{ relation }} as s
    {%- endif -%}
{%- endmacro %}

{#- `a is distinct from b`, written so both warehouses run it (Redshift has no
    IS DISTINCT FROM): true when exactly one is null, or both are set and
    differ; false otherwise, never null. -#}
{% macro distinct_from(a, b) -%}
    (coalesce(({{ a }}) <> ({{ b }}), false) or (({{ a }}) is null) <> (({{ b }}) is null))
{%- endmacro %}
