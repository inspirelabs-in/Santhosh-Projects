UPDATE roles
SET scoring_rubric = jsonb_set(
  jsonb_set(
    jsonb_set(
      jsonb_set(
        jsonb_set(
          jsonb_set(
            jsonb_set(
              scoring_rubric,
              '{scheduling,enabled}', 'true'::jsonb
            ),
            '{scheduling,horizon_business_days}', '5'::jsonb
          ),
          '{scheduling,min_lead_hours}', '2'::jsonb
        ),
        '{scheduling,rounds,technical,panel_emails}', '["santhosh28203@gmail.com"]'::jsonb
      ),
      '{scheduling,rounds,ceo,panel_emails}', '["santhosh28203@gmail.com"]'::jsonb
    ),
    '{scheduling,rounds,hr,panel_emails}', '["santhosh28203@gmail.com"]'::jsonb
  ),
  '{agentic,voice_screening_enabled}', 'true'::jsonb
)
WHERE id = '07be4ffa-3a4a-4a77-803c-03e90bb81cf5';

INSERT INTO panel_members (name, email, role_type, calendar_provider, is_active)
VALUES
  ('Santhosh Test', 'santhosh28203@gmail.com', 'technical', 'google', true),
  ('Santhosh Test', 'santhosh28203@gmail.com', 'ceo',       'google', true),
  ('Santhosh Test', 'santhosh28203@gmail.com', 'hr',        'google', true);

SELECT scoring_rubric -> 'scheduling' AS sch FROM roles WHERE id = '07be4ffa-3a4a-4a77-803c-03e90bb81cf5';
SELECT email, role_type, is_active FROM panel_members;
