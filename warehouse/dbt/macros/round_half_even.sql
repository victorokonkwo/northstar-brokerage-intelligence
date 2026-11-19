{#- Round a numeric expression half-even (banker's rounding) to `scale` places.
    ADR 004 makes ROUND_HALF_EVEN the only rounding mode, and Postgres round()
    rounds halves away from zero, so reporting outputs round through this macro.
    floor() keeps it correct for negative values: -2.5 -> -2, -3.5 -> -4. -#}
{% macro round_half_even(expression, scale) -%}
    {%- set factor = 10 ** scale -%}
    {%- set scaled = "((" ~ expression ~ ") * " ~ factor ~ ")" -%}
    (
        case
            when {{ scaled }} - floor({{ scaled }}) > 0.5 then floor({{ scaled }}) + 1
            when {{ scaled }} - floor({{ scaled }}) < 0.5 then floor({{ scaled }})
            when mod(floor({{ scaled }}), 2) = 0 then floor({{ scaled }})
            else floor({{ scaled }}) + 1
        end / {{ factor }}
    )::numeric(18, {{ scale }})
{%- endmacro %}
