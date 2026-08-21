export type VibecodeSnapshotModel = {
  display_name: string;
  id: string;
  is_image: boolean;
  object: string;
  owned_by: string;
  pricing: {
    currency?: string;
    cache_create_usd_per_m?: number;
    cache_read_usd_per_m?: number;
    input_usd_per_m?: number;
    output_usd_per_m?: number;
    usd_per_image?: number;
  };
};

export const VIBECODE_MODELS_SNAPSHOT: VibecodeSnapshotModel[] = [
  {
    "display_name": "GPT 5.6 Sol",
    "id": "gpt-5.6-sol",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.042646,
      "currency": "usd",
      "input_usd_per_m": 0.426462,
      "output_usd_per_m": 2.558774
    }
  },
  {
    "display_name": "GPT 5.6 Terra",
    "id": "gpt-5.6-terra",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.017058,
      "currency": "usd",
      "input_usd_per_m": 0.170585,
      "output_usd_per_m": 1.02351
    }
  },
  {
    "display_name": "GPT 5.6 Luna",
    "id": "gpt-5.6-luna",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.002624,
      "currency": "usd",
      "input_usd_per_m": 0.026244,
      "output_usd_per_m": 0.157463
    }
  },
  {
    "display_name": "GPT 5.5",
    "id": "gpt-5.5",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.042646,
      "currency": "usd",
      "input_usd_per_m": 0.426462,
      "output_usd_per_m": 2.558774
    }
  },
  {
    "display_name": "GPT-5.5 OpenAI compact",
    "id": "gpt-5.5-openai-compact",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.06561,
      "currency": "usd",
      "input_usd_per_m": 0.656096,
      "output_usd_per_m": 3.936576
    }
  },
  {
    "display_name": "GPT 5.4 Mini",
    "id": "gpt-5.4-mini",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.006397,
      "currency": "usd",
      "input_usd_per_m": 0.063969,
      "output_usd_per_m": 0.383816
    }
  },
  {
    "display_name": "Codex Auto Review",
    "id": "codex-auto-review",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.021323,
      "currency": "usd",
      "input_usd_per_m": 0.213231,
      "output_usd_per_m": 1.279387
    }
  },
  {
    "display_name": "Claude Fable 5",
    "id": "claude-fable-5",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 5.881919,
      "cache_read_usd_per_m": 0.470554,
      "currency": "usd",
      "input_usd_per_m": 4.705535,
      "output_usd_per_m": 23.527676
    }
  },
  {
    "display_name": "Claude Haiku 4.5",
    "id": "claude-haiku-4-5",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.588192,
      "cache_read_usd_per_m": 0.047055,
      "currency": "usd",
      "input_usd_per_m": 0.470554,
      "output_usd_per_m": 2.352768
    }
  },
  {
    "display_name": "Claude Sonnet 4.6",
    "id": "claude-sonnet-4-6",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.369054,
      "cache_read_usd_per_m": 0.029524,
      "currency": "usd",
      "input_usd_per_m": 0.295243,
      "output_usd_per_m": 1.476216
    }
  },
  {
    "display_name": "Claude Sonnet 5",
    "id": "claude-sonnet-5",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.246036,
      "cache_read_usd_per_m": 0.019683,
      "currency": "usd",
      "input_usd_per_m": 0.196829,
      "output_usd_per_m": 0.984144
    }
  },
  {
    "display_name": "Claude Opus 4.6",
    "id": "claude-opus-4-6",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.61509,
      "cache_read_usd_per_m": 0.049207,
      "currency": "usd",
      "input_usd_per_m": 0.492072,
      "output_usd_per_m": 2.46036
    }
  },
  {
    "display_name": "Claude Opus 4.7",
    "id": "claude-opus-4-7",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.61509,
      "cache_read_usd_per_m": 0.049207,
      "currency": "usd",
      "input_usd_per_m": 0.492072,
      "output_usd_per_m": 2.46036
    }
  },
  {
    "display_name": "Claude Opus 4.8",
    "id": "claude-opus-4-8",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.61509,
      "cache_read_usd_per_m": 0.049207,
      "currency": "usd",
      "input_usd_per_m": 0.492072,
      "output_usd_per_m": 2.46036
    }
  },
  {
    "display_name": "Claude Opus 5",
    "id": "claude-opus-5",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.61509,
      "cache_read_usd_per_m": 0.049207,
      "currency": "usd",
      "input_usd_per_m": 0.492072,
      "output_usd_per_m": 2.46036
    }
  },
  {
    "display_name": "Grok 4.5",
    "id": "grok-4-5",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.06561,
      "cache_read_usd_per_m": 0.016402,
      "currency": "usd",
      "input_usd_per_m": 0.06561,
      "output_usd_per_m": 0.196829
    }
  },
  {
    "display_name": "Grok 4.6",
    "id": "grok-4-6",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.06561,
      "cache_read_usd_per_m": 0.016402,
      "currency": "usd",
      "input_usd_per_m": 0.06561,
      "output_usd_per_m": 0.196829
    }
  },
  {
    "display_name": "Kimi K3",
    "id": "kimi-k3",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.164024,
      "cache_read_usd_per_m": 0.016402,
      "currency": "usd",
      "input_usd_per_m": 0.164024,
      "output_usd_per_m": 0.590486
    }
  },
  {
    "display_name": "Gemini 3 Flash",
    "id": "gemini-3-flash-preview",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.0,
      "currency": "usd",
      "input_usd_per_m": 0.098414,
      "output_usd_per_m": 0.590486
    }
  },
  {
    "display_name": "Gemini 3.1 Pro",
    "id": "gemini-3.1-pro-preview",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.0,
      "currency": "usd",
      "input_usd_per_m": 0.393658,
      "output_usd_per_m": 2.361946
    }
  },
  {
    "display_name": "Gemini 3.5 Flash",
    "id": "gemini-3.5-flash",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.0,
      "currency": "usd",
      "input_usd_per_m": 0.295243,
      "output_usd_per_m": 1.771459
    }
  },
  {
    "display_name": "Gemini 3.6 Flash",
    "id": "gemini-3.6-flash",
    "is_image": false,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "cache_create_usd_per_m": 0.0,
      "cache_read_usd_per_m": 0.0,
      "currency": "usd",
      "input_usd_per_m": 0.295243,
      "output_usd_per_m": 1.476216
    }
  },
  {
    "display_name": "GPT Image 2",
    "id": "gpt-image-2-vip",
    "is_image": true,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "currency": "usd",
      "usd_per_image": 0.042646
    }
  },
  {
    "display_name": "Nano Banana",
    "id": "nano-banana",
    "is_image": true,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "currency": "usd",
      "usd_per_image": 0.045927
    }
  },
  {
    "display_name": "Nano Banana 2 (1K/2K/4K)",
    "id": "nano-banana-2",
    "is_image": true,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "currency": "usd",
      "usd_per_image": 0.039366
    }
  },
  {
    "display_name": "Nano Banana Pro (1K/2K/4K)",
    "id": "nano-banana-pro",
    "is_image": true,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "currency": "usd",
      "usd_per_image": 0.059049
    }
  },
  {
    "display_name": "Nano Banana 2 Lite",
    "id": "nano-banana-2-lite",
    "is_image": true,
    "object": "model",
    "owned_by": "vibecode",
    "pricing": {
      "currency": "usd",
      "usd_per_image": 0.016402
    }
  }
];
