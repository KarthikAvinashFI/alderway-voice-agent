-- Seed data for local runs. Every person, partner and property here is invented.
--
-- Numbers are in the 555 range reserved for fiction, and no address is a real one. Nothing in this
-- file should ever be dialled.

INSERT INTO lender_partners (partner_id, name, contact_phone, referral_label) VALUES
  ('lp_westmarch', 'Westmarch Home Lending',   '+18005550140', 'your mortgage lender'),
  ('lp_pinehollow', 'Pine Hollow Mortgage Co', '+18005550141', 'your loan officer'),
  ('lp_ashfordsvc', 'Ashford Loan Servicing',  '+18005550142', 'your mortgage servicer');

INSERT INTO campaigns (campaign_id, name, questionnaire, questionnaire_version, reference_year, max_attempts) VALUES
  ('cmp_renewal_q3', 'Renewal shopping, quarter three', 'home_quote_intake', '3.2', 2026, 4),
  ('cmp_preclose',   'Pre closing insurance placement',  'home_quote_intake', '3.2', 2026, 3);

-- A spread worth calling: an ordinary quotable house, one that will disqualify on its roof, one whose
-- claims history disqualifies, an out of area property, and one already on the suppression list so the
-- guard against dialling it has something to catch.
INSERT INTO leads (lead_id, campaign_id, partner_id, full_name, phone, property_address_hint, property_state, partner_reference, time_zone, status) VALUES
  ('led_halloway',   'cmp_renewal_q3', 'lp_westmarch',  'Marguerite Halloway', '+16145550118', '2841 Wexford Lane, Dublin, Ohio 43017',      'OH', 'WM-4417',  'America/New_York',            'pending'),
  ('led_achterberg', 'cmp_preclose',   'lp_pinehollow', 'Desmond Achterberg',  '+14695550473', '1190 Calloway Bend, Plano, Texas 75024',     'TX', 'PH-88120', 'America/Chicago',             'pending'),
  ('led_bramwell',   'cmp_renewal_q3', 'lp_ashfordsvc', 'Ottoline Bramwell',   '+13175550692', '664 Harlow Street, Carmel, Indiana 46032',   'IN', 'AS-2094',  'America/Indiana/Indianapolis', 'pending'),
  ('led_nettlefold', 'cmp_renewal_q3', 'lp_westmarch',  'Corwin Nettlefold',   '+18655550845', '3306 Ridgemount Drive, Knoxville, Tennessee 37919', 'TN', 'WM-4418', 'America/New_York', 'pending'),
  ('led_follett',    'cmp_preclose',   'lp_pinehollow', 'Ignatius Follett',    '+14145550377', '8802 Fenwick Row, Waukesha, Wisconsin 53188', 'WI', 'PH-88121', 'America/Chicago',            'pending'),
  ('led_okonjo',     'cmp_renewal_q3', 'lp_ashfordsvc', 'Adaeze Okonjo',       '+15035550266', '75 Marberry Court, Portland, Oregon 97209',  'OR', 'AS-2095',  'America/Los_Angeles',         'pending'),
  ('led_vane',       'cmp_renewal_q3', 'lp_westmarch',  'Perpetua Vane',       '+16155550231', '412 Sableridge Way, Franklin, Tennessee 37064', 'TN', 'WM-4419', 'America/Chicago',          'suppressed');

INSERT INTO do_not_call (dnc_id, phone, reason, source, verbatim) VALUES
  ('dnc_vane', '+16155550231', 'caller asked to be removed on a previous attempt', 'caller_request',
   'Take me off your list and do not call me again.');

-- One lead mid intake, so resume-from-drop can be exercised without first placing a call. The answers
-- stop where a real call would have dropped: consent given, address confirmed, nothing after it.
INSERT INTO intake_sessions (session_id, lead_id, questionnaire, questionnaire_version, state) VALUES
  ('ses_okonjo_partial', 'led_okonjo', 'home_quote_intake', '3.2', 'in_progress');

INSERT INTO answers (answer_id, session_id, field_id, value_text, value_type, status, verbatim, confidence, sequence) VALUES
  ('ans_ok_1', 'ses_okonjo_partial', 'reached_right_party',      'homeowner', 'str',  'answered', 'Yes, that is me.',                          'high', 1),
  ('ans_ok_2', 'ses_okonjo_partial', 'homeowner_confirmed',      'True',      'bool', 'answered', 'Yes, we own it.',                            'high', 2),
  ('ans_ok_3', 'ses_okonjo_partial', 'recording_disclosure_ack', 'True',      'bool', 'answered', 'That is fine.',                              'high', 3),
  ('ans_ok_4', 'ses_okonjo_partial', 'consent_to_continue',      'True',      'bool', 'answered', 'Go ahead, I have a few minutes.',            'high', 4),
  ('ans_ok_5', 'ses_okonjo_partial', 'contact_name',             'Adaeze Okonjo', 'str', 'answered', 'Adaeze Okonjo.',                          'high', 5),
  ('ans_ok_6', 'ses_okonjo_partial', 'property_address',         '75 Marberry Court, Portland, Oregon 97209', 'str', 'answered',
   'Seventy five Marberry Court, Portland, Oregon, nine seven two zero nine.', 'high', 6),
  ('ans_ok_7', 'ses_okonjo_partial', 'property_state',           'Oregon',    'str',  'answered', 'Oregon.',                                    'high', 7);

INSERT INTO call_attempts (call_id, lead_id, session_id, attempt_number, status, disposition, ended_reason, duration_seconds) VALUES
  ('cal_okonjo_1', 'led_okonjo', 'ses_okonjo_partial', 1, 'completed', 'dropped', 'line dropped mid intake', 96);

UPDATE leads SET attempts = 1, status = 'retry' WHERE lead_id = 'led_okonjo';

INSERT INTO consent_events (consent_id, lead_id, call_id, kind, granted, verbatim) VALUES
  ('con_ok_1', 'led_okonjo', 'cal_okonjo_1', 'recording', TRUE, 'That is fine.'),
  ('con_ok_2', 'led_okonjo', 'cal_okonjo_1', 'continue',  TRUE, 'Go ahead, I have a few minutes.');
