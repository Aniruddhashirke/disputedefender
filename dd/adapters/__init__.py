from .etsy import EtsyAdapter

ADAPTERS = {"etsy": EtsyAdapter}


def get_adapter(platform: str):
    try:
        return ADAPTERS[platform.lower()]()
    except KeyError:
        raise ValueError(f"No adapter for platform '{platform}'. Available: {sorted(ADAPTERS)}. eBay and Shopify are planned.")
