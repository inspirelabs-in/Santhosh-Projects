export const PI_PERSONAS = [
  "Altruist",
  "Captain",
  "Collaborator",
  "Controller",
  "Craftsman",
  "Guardian",
  "Individualist",
  "Maverick",
  "Operator",
  "Persuader",
  "Promoter",
  "Scholar",
  "Specialist",
  "Strategist",
  "Venturer",
  "Adapter",
  "Analyzer",
] as const;

export type PiPersona = (typeof PI_PERSONAS)[number];
