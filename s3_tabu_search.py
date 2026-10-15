from __future__ import annotations

import math
import time
from collections import deque
from typing import Deque, Dict, List, Tuple, Iterable, Set, Optional

import pandas as pd


# =========================
# BASELINE: S3 + DUPLICATIONSlp
# =========================

BASE_SCHEMA = "S3"

DUPES = {
    "D1": "Copy Customer[name, email] -> Order",
    "D2": "Copy Address[city, state] -> Order",
    "D3": "Copy Coupon[code, discountValue] -> Order",
    "D4": "Copy Product[name, price] -> OrderItem",
    "D5": "Copy Seller[storeName] -> OrderItem (OrderItem -> Product -> Seller)",
    "D6": "Copy Product[name, price] -> CartItem",
    "D7": "Copy Product[name, price] -> Wishlist (product summary array)",
    "D8": "Copy Seller[storeName, rating] -> Product",
    "D9": "Copy Category[name] -> Product",
    "D10": "Copy Customer[name] -> Review",
    "D11": "Copy Product[name] -> Review",
    "D12": "Copy Order[orderDate, status] -> Payment",
    "D13": "Copy Order[orderDate, status] -> Shipment",
    "D14": "Copy Order[orderDate, status] -> OrderItem",
}
DUPE_IDS = list(DUPES.keys())

BASE_COLLECTIONS = [
    "customers",
    "addresses",
    "sellers",
    "products",
    "categories",
    "carts",
    "cart_items",
    "orders",
    "order_items",
    "payments",
    "shipments",
    "reviews",
    "coupons",
    "wishlists",
]


VOLUMES = {
    "V1_properties_counts": dict(
        customers=1000,
        addresses=1500,
        sellers=200,
        products=5000,
        categories=100,
        carts=1000,
        cart_items=4000,
        orders=10000,
        order_items=30000,
        payments=10000,
        shipments=10000,
        reviews=15000,
        coupons=250,
        wishlists=1000,
    ),
}

# Assumptions required by embedded-array duplication costs that are not explicit in .properties
DEFAULT_WISHLIST_ITEMS_PER_WISHLIST = 5.0
DEFAULT_COUPON_USAGE_RATIO = 0.25

# =========================
# PARÂMETROS FÍSICOS E CACHE
# =========================

BLOCK_SIZE_BYTES = 4096
MEM_DATA_MB = 256
MEM_INDEX_MB = 256
MEM_DATA_BYTES = MEM_DATA_MB * 1024 * 1024
MEM_INDEX_BYTES = MEM_INDEX_MB * 1024 * 1024

Td = 1.0
Tm = 0.01
LAMBDA_WRITE = 1.0

INDEX_FACTOR = {c: 0.1 for c in BASE_COLLECTIONS}

AVG_DOC_SIZE_BASE = {
    "customers": 180,
    "addresses": 100,
    "sellers": 160,
    "products": 220,
    "categories": 90,
    "carts": 120,
    "cart_items": 90,
    "orders": 180,
    "order_items": 100,
    "payments": 100,
    "shipments": 110,
    "reviews": 130,
    "coupons": 90,
    "wishlists": 120,
}

# Tamanho médio do valor, não "tamanho BSON fixo"
AVG_VALUE_SIZE = {
    "customer_name": 40,
    "customer_email": 50,
    "city": 30,
    "state": 20,
    "coupon_code": 20,
    "discount_value": 8,   # double
    "product_name": 50,
    "product_price": 16,   # Decimal128 mais realista para preço
    "seller_storeName": 40,
    "seller_rating": 8,    # double
    "category_name": 30,
    "order_date": 8,       # BSON Date
    "order_status": 20,
}

# =========================
# BSON SIZE HELPERS
# =========================

def bson_cstring_size(field_name: str) -> int:
    return len(field_name.encode("utf-8")) + 1


def bson_scalar_field_size(field_name: str, value_size: int) -> int:
    return 1 + bson_cstring_size(field_name) + value_size


def bson_string_field_size(field_name: str, avg_content_size: int) -> int:
    return 1 + bson_cstring_size(field_name) + 4 + avg_content_size + 1


def bson_document_overhead() -> int:
    return 5


def bson_embedded_document_field_size(field_name: str, embedded_payload_size: int) -> int:
    return 1 + bson_cstring_size(field_name) + embedded_payload_size


# =========================
# WORKLOADS
# =========================

WORKLOADS = {
    "W4_hybrid": {
        "alpha_read": 0.60,
        "read_mix": {
            "Q1": 0.12,
            "Q2": 0.11,
            "Q3": 0.08,
            "Q4": 0.05,
            "Q5": 0.10,
            "Q6": 0.05,
            "Q7": 0.07,
            "Q8": 0.05,
            "Q9": 0.05,
            "Q10": 0.05,
            "Q11": 0.06,
            "Q12": 0.07,
            "Q13": 0.08,
            "Q14": 0.06,
        },
        "write_mix": {
            "U1_update_customer_name": 0.18,
            "U2_update_product_core": 0.24,
            "U3_update_seller_profile": 0.10,
            "U4_update_category_name": 0.08,
            "U5_update_order_status": 0.20,
            "C1_create_order": 0.12,
            "C2_add_review": 0.08,
        },
    },
}

# =========================
# FLAGS / SCHEMA NAME
# =========================

def flags_to_schema_name(flags: Dict[str, bool]) -> str:
    enabled = [k for k in DUPE_IDS if flags.get(k, False)]
    return BASE_SCHEMA if not enabled else BASE_SCHEMA + "_" + "_".join(enabled)


def make_base_flags() -> Dict[str, bool]:
    return {k: False for k in DUPE_IDS}


def flags_to_key(flags: Dict[str, bool]) -> Tuple[int, ...]:
    return tuple(int(flags[k]) for k in DUPE_IDS)


def copy_flags(flags: Dict[str, bool]) -> Dict[str, bool]:
    return {k: bool(v) for k, v in flags.items()}


def generate_neighbors(flags: Dict[str, bool]) -> Iterable[Tuple[str, Dict[str, bool]]]:
    for dupe_id in DUPE_IDS:
        neighbor = copy_flags(flags)
        neighbor[dupe_id] = not neighbor[dupe_id]
        yield dupe_id, neighbor


# =========================
# READ PATTERNS
# =========================

BASE_READ_PATTERNS: Dict[str, List[Tuple[str, int]]] = {
    "Q1": [("orders", 1), ("customers", 1), ("addresses", 1), ("coupons", 1)],
    "Q2": [("orders", 1)],
    "Q3": [("payments", 1)],
    "Q4": [("shipments", 1)],
    "Q5": [("order_items", 1), ("orders", 1), ("products", 1), ("sellers", 1)],
    "Q6": [("carts", 1), ("cart_items", 1), ("products", 1)],
    "Q7": [("wishlists", 1), ("products", 1)],
    "Q8": [("products", 1), ("sellers", 1), ("categories", 1)],
    "Q9": [("products", 1)],
    "Q10": [("products", 1)],
    "Q11": [("reviews", 1), ("customers", 1), ("products", 1)],
    "Q12": [("reviews", 1), ("products", 1)],
    "Q13": [("payments", 1), ("orders", 1)],
    "Q14": [("shipments", 1), ("orders", 1)],
}


def _remove_once(path: List[Tuple[str, int]], collection: str) -> None:
    for idx, (c, _) in enumerate(path):
        if c == collection:
            del path[idx]
            return


def build_read_patterns(flags: Dict[str, bool]) -> Dict[str, List[Tuple[str, int]]]:
    patterns = {q: list(v) for q, v in BASE_READ_PATTERNS.items()}

    if flags.get("D1"):
        _remove_once(patterns["Q1"], "customers")
    if flags.get("D2"):
        _remove_once(patterns["Q1"], "addresses")
    if flags.get("D3"):
        _remove_once(patterns["Q1"], "coupons")
    if flags.get("D14"):
        _remove_once(patterns["Q5"], "orders")
    if flags.get("D4") and flags.get("D5"):
        _remove_once(patterns["Q5"], "products")
    if flags.get("D5"):
        _remove_once(patterns["Q5"], "sellers")
    if flags.get("D6"):
        _remove_once(patterns["Q6"], "products")
    if flags.get("D7"):
        _remove_once(patterns["Q7"], "products")
    if flags.get("D8"):
        _remove_once(patterns["Q8"], "sellers")
    if flags.get("D9"):
        _remove_once(patterns["Q8"], "categories")
    if flags.get("D10"):
        _remove_once(patterns["Q11"], "customers")
    if flags.get("D11"):
        _remove_once(patterns["Q11"], "products")
        _remove_once(patterns["Q12"], "products")
    if flags.get("D12"):
        _remove_once(patterns["Q13"], "orders")
    if flags.get("D13"):
        _remove_once(patterns["Q14"], "orders")

    return patterns


# =========================
# WRITE PATTERNS
# =========================

BASE_WRITE_PATTERNS: Dict[str, List[Dict]] = {
    "U1_update_customer_name": [{"collection": "customers", "reads": 1, "writes": 1}],
    "U2_update_product_core": [{"collection": "products", "reads": 1, "writes": 1}],
    "U3_update_seller_profile": [{"collection": "sellers", "reads": 1, "writes": 1}],
    "U4_update_category_name": [{"collection": "categories", "reads": 1, "writes": 1}],
    "U5_update_order_status": [{"collection": "orders", "reads": 1, "writes": 1}],
    "C1_create_order": [
        {"collection": "orders", "reads": 1, "writes": 1},
        {"collection": "order_items", "reads": 1, "writes": 1},
        {"collection": "payments", "reads": 1, "writes": 1},
        {"collection": "shipments", "reads": 1, "writes": 1},
    ],
    "C2_add_review": [{"collection": "reviews", "reads": 1, "writes": 1}],
}


def derive_volume_stats(volume_params: dict) -> Dict[str, float]:
    customers = max(1.0, float(volume_params["customers"]))
    sellers = max(1.0, float(volume_params["sellers"]))
    products = max(1.0, float(volume_params["products"]))
    categories = max(1.0, float(volume_params["categories"]))
    orders = max(1.0, float(volume_params["orders"]))
    wishlists = max(1.0, float(volume_params["wishlists"]))

    return {
        "avg_addr_per_customer": float(volume_params["addresses"]) / customers,
        "avg_orders_per_customer": float(volume_params["orders"]) / customers,
        "avg_items_per_order": float(volume_params["order_items"]) / orders,
        "avg_products_per_seller": float(volume_params["products"]) / sellers,
        "avg_products_per_category": float(volume_params["products"]) / categories,
        "avg_cart_items_per_customer": float(volume_params["cart_items"]) / customers,
        "avg_cart_items_per_product": float(volume_params["cart_items"]) / products,
        "avg_wishlist_items_per_wishlist": DEFAULT_WISHLIST_ITEMS_PER_WISHLIST,
        "avg_reviews_per_product": float(volume_params["reviews"]) / products,
        "avg_reviews_per_customer": float(volume_params["reviews"]) / customers,
        "avg_order_items_per_product": float(volume_params["order_items"]) / products,
        "avg_order_items_per_seller": float(volume_params["order_items"]) / sellers,
        "payments_per_order": float(volume_params["payments"]) / orders,
        "shipments_per_order": float(volume_params["shipments"]) / orders,
        "coupon_usage_ratio": DEFAULT_COUPON_USAGE_RATIO,
        "avg_wishlists_per_customer": wishlists / customers,
        "avg_wishlists_per_product": (wishlists * DEFAULT_WISHLIST_ITEMS_PER_WISHLIST) / products,
        "avg_created_items_per_order": 2.0,
    }


def build_write_patterns(flags: Dict[str, bool], volume_params: dict) -> Dict[str, List[Dict]]:
    stats = derive_volume_stats(volume_params)
    avg_orders = stats["avg_orders_per_customer"]
    avg_order_items_per_product = stats["avg_order_items_per_product"]
    avg_order_items_per_seller = stats["avg_order_items_per_seller"]
    avg_order_items_created = stats["avg_created_items_per_order"]
    avg_cart_items_per_product = stats["avg_cart_items_per_product"]
    avg_wishlists_per_product = stats["avg_wishlists_per_product"]
    avg_reviews_product = stats["avg_reviews_per_product"]
    avg_reviews_customer = stats["avg_reviews_per_customer"]
    avg_products_seller = stats["avg_products_per_seller"]
    avg_products_category = stats["avg_products_per_category"]

    patterns = {op: [dict(x) for x in lst] for op, lst in BASE_WRITE_PATTERNS.items()}

    patterns["C1_create_order"] = [
        {"collection": "orders", "reads": 0, "writes": 1},
        {"collection": "order_items", "reads": 0, "writes": avg_order_items_created},
        {"collection": "payments", "reads": 0, "writes": 1},
        {"collection": "shipments", "reads": 0, "writes": 1},
    ]

    if flags.get("D1"):
        patterns["U1_update_customer_name"].append({"collection": "orders", "reads": 0, "writes": avg_orders})
    if flags.get("D10"):
        patterns["U1_update_customer_name"].append({"collection": "reviews", "reads": 0, "writes": avg_reviews_customer})

    if flags.get("D4"):
        patterns["U2_update_product_core"].append({"collection": "order_items", "reads": 0, "writes": avg_order_items_per_product})
    if flags.get("D6"):
        patterns["U2_update_product_core"].append({"collection": "cart_items", "reads": 0, "writes": avg_cart_items_per_product})
    if flags.get("D7"):
        patterns["U2_update_product_core"].append({"collection": "wishlists", "reads": avg_wishlists_per_product, "writes": avg_wishlists_per_product})
    if flags.get("D11"):
        patterns["U2_update_product_core"].append({"collection": "reviews", "reads": 0, "writes": avg_reviews_product})

    if flags.get("D8"):
        patterns["U3_update_seller_profile"].append({"collection": "products", "reads": 0, "writes": avg_products_seller})
    if flags.get("D5"):
        patterns["U3_update_seller_profile"].append({"collection": "products", "reads": avg_products_seller, "writes": 0})
        patterns["U3_update_seller_profile"].append({"collection": "order_items", "reads": 0, "writes": avg_order_items_per_seller})

    if flags.get("D9"):
        patterns["U4_update_category_name"].append({"collection": "products", "reads": 0, "writes": avg_products_category})

    if flags.get("D12"):
        patterns["U5_update_order_status"].append({"collection": "payments", "reads": 0, "writes": stats["payments_per_order"]})
    if flags.get("D13"):
        patterns["U5_update_order_status"].append({"collection": "shipments", "reads": 0, "writes": stats["shipments_per_order"]})
    if flags.get("D14"):
        patterns["U5_update_order_status"].append({"collection": "order_items", "reads": 0, "writes": stats["avg_items_per_order"]})

    if flags.get("D1"):
        patterns["C1_create_order"].append({"collection": "customers", "reads": 1, "writes": 0})
    if flags.get("D2"):
        patterns["C1_create_order"].append({"collection": "addresses", "reads": 1, "writes": 0})
    if flags.get("D3"):
        patterns["C1_create_order"].append({"collection": "coupons", "reads": stats["coupon_usage_ratio"], "writes": 0})

    patterns["C1_create_order"].append({"collection": "products", "reads": avg_order_items_created, "writes": 0})

    if flags.get("D5"):
        patterns["C1_create_order"].append({"collection": "sellers", "reads": avg_order_items_created, "writes": 0})

    if flags.get("D10"):
        patterns["C2_add_review"].append({"collection": "customers", "reads": 1, "writes": 0})
    if flags.get("D11"):
        patterns["C2_add_review"].append({"collection": "products", "reads": 1, "writes": 0})

    return patterns


# =========================
# DOC SIZES
# =========================

def derive_avg_doc_sizes(flags: Dict[str, bool], volume_params: dict) -> Dict[str, int]:
    stats = derive_volume_stats(volume_params)
    avg_wishlist_items = stats["avg_wishlist_items_per_wishlist"]

    sizes = dict(AVG_DOC_SIZE_BASE)
    for coll in sizes:
        sizes[coll] += bson_document_overhead()

    int_field = lambda name: bson_scalar_field_size(name, 4)
    dbl_field = lambda name: bson_scalar_field_size(name, 8)
    date_field = lambda name: bson_scalar_field_size(name, AVG_VALUE_SIZE["order_date"])
    str_field = lambda name, val: bson_string_field_size(name, val)

    def customer_summary_size() -> int:
        payload = (
            bson_document_overhead()
            + int_field("customerId")
            + str_field("name", AVG_VALUE_SIZE["customer_name"])
            + str_field("email", AVG_VALUE_SIZE["customer_email"])
        )
        return bson_embedded_document_field_size("customerSummary", payload)

    def address_summary_size() -> int:
        payload = (
            bson_document_overhead()
            + int_field("addressId")
            + str_field("city", AVG_VALUE_SIZE["city"])
            + str_field("state", AVG_VALUE_SIZE["state"])
        )
        return bson_embedded_document_field_size("addressSummary", payload)

    def coupon_summary_size() -> int:
        payload = (
            bson_document_overhead()
            + int_field("couponId")
            + str_field("code", AVG_VALUE_SIZE["coupon_code"])
            + dbl_field("discountValue")
        )
        return bson_embedded_document_field_size("couponSummary", payload)

    def product_summary_size(field_name: str = "productSummary") -> int:
        payload = (
            bson_document_overhead()
            + int_field("productId")
            + str_field("name", AVG_VALUE_SIZE["product_name"])
            + bson_scalar_field_size("price", AVG_VALUE_SIZE["product_price"])
        )
        return bson_embedded_document_field_size(field_name, payload)

    def seller_summary_size(field_name: str = "sellerSummary", with_rating: bool = False) -> int:
        payload = (
            bson_document_overhead()
            + int_field("sellerId")
            + str_field("storeName", AVG_VALUE_SIZE["seller_storeName"])
        )
        if with_rating:
            payload += dbl_field("rating")
        return bson_embedded_document_field_size(field_name, payload)

    def category_summary_size() -> int:
        payload = (
            bson_document_overhead()
            + int_field("categoryId")
            + str_field("name", AVG_VALUE_SIZE["category_name"])
        )
        return bson_embedded_document_field_size("categorySummary", payload)

    def review_customer_summary_size() -> int:
        payload = bson_document_overhead() + int_field("customerId") + str_field("name", AVG_VALUE_SIZE["customer_name"])
        return bson_embedded_document_field_size("customerSummary", payload)

    def review_product_summary_size() -> int:
        payload = bson_document_overhead() + int_field("productId") + str_field("name", AVG_VALUE_SIZE["product_name"])
        return bson_embedded_document_field_size("productSummary", payload)

    def order_summary_size(field_name: str = "orderSummary") -> int:
        payload = (
            bson_document_overhead()
            + int_field("orderId")
            + date_field("orderDate")
            + str_field("status", AVG_VALUE_SIZE["order_status"])
        )
        return bson_embedded_document_field_size(field_name, payload)

    def product_summary_array_size(n: float) -> int:
        per_item_doc = (
            bson_document_overhead()
            + int_field("productId")
            + str_field("name", AVG_VALUE_SIZE["product_name"])
            + bson_scalar_field_size("price", AVG_VALUE_SIZE["product_price"])
        )
        array_payload = bson_document_overhead() + int(n * per_item_doc)
        return bson_embedded_document_field_size("productSummaries", array_payload)

    if flags.get("D1"):
        sizes["orders"] += customer_summary_size()
    if flags.get("D2"):
        sizes["orders"] += address_summary_size()
    if flags.get("D3"):
        coupon_ratio = stats["coupon_usage_ratio"]
        sizes["orders"] += int(coupon_ratio * coupon_summary_size())
    if flags.get("D4"):
        sizes["order_items"] += product_summary_size()
    if flags.get("D5"):
        sizes["order_items"] += seller_summary_size(with_rating=False)
    if flags.get("D6"):
        sizes["cart_items"] += product_summary_size()
    if flags.get("D7"):
        sizes["wishlists"] += product_summary_array_size(avg_wishlist_items)
    if flags.get("D8"):
        sizes["products"] += seller_summary_size(with_rating=True)
    if flags.get("D9"):
        sizes["products"] += category_summary_size()
    if flags.get("D10"):
        sizes["reviews"] += review_customer_summary_size()
    if flags.get("D11"):
        sizes["reviews"] += review_product_summary_size()
    if flags.get("D12"):
        sizes["payments"] += order_summary_size()
    if flags.get("D13"):
        sizes["shipments"] += order_summary_size()
    if flags.get("D14"):
        sizes["order_items"] += order_summary_size()

    return sizes


# =========================
# COST MODEL CORE
# =========================

def estimate_coll_stats(avg_sizes: Dict[str, int], volume_params: dict) -> Dict[str, Dict]:
    doc_counts = {
        "customers": int(volume_params["customers"]),
        "addresses": int(volume_params["addresses"]),
        "sellers": int(volume_params["sellers"]),
        "products": int(volume_params["products"]),
        "categories": int(volume_params["categories"]),
        "carts": int(volume_params["carts"]),
        "cart_items": int(volume_params["cart_items"]),
        "orders": int(volume_params["orders"]),
        "order_items": int(volume_params["order_items"]),
        "payments": int(volume_params["payments"]),
        "shipments": int(volume_params["shipments"]),
        "reviews": int(volume_params["reviews"]),
        "coupons": int(volume_params["coupons"]),
        "wishlists": int(volume_params["wishlists"]),
    }

    def make_stats(coll: str, n_docs: int) -> Dict:
        storage = n_docs * avg_sizes[coll]
        index = storage * INDEX_FACTOR[coll]
        B_d = max(1, math.ceil(storage / BLOCK_SIZE_BYTES))
        B_i = max(1, math.ceil(index / BLOCK_SIZE_BYTES))
        return {
            "n_docs": n_docs,
            "storage_size": storage,
            "index_size": index,
            "B_d": B_d,
            "B_i": B_i,
        }

    return {coll: make_stats(coll, n_docs) for coll, n_docs in doc_counts.items()}


def allocate_memory_blocks_per_schema(coll_stats: Dict[str, Dict]) -> Dict[str, Dict]:
    total_data_blocks = MEM_DATA_BYTES // BLOCK_SIZE_BYTES
    total_index_blocks = MEM_INDEX_BYTES // BLOCK_SIZE_BYTES

    total_storage = sum(cs["storage_size"] for cs in coll_stats.values())
    total_index = sum(cs["index_size"] for cs in coll_stats.values())

    mem: Dict[str, Dict] = {}
    for base, cs in coll_stats.items():
        share_data = (cs["storage_size"] / total_storage) if total_storage > 0 else 0.0
        share_index = (cs["index_size"] / total_index) if total_index > 0 else 0.0
        M_d = min(cs["B_d"], int(total_data_blocks * share_data))
        M_i = min(cs["B_i"], int(total_index_blocks * share_index))
        mem[base] = {"M_d": M_d, "M_i": M_i}
    return mem


def cost_rand(base: str, coll_stats: Dict[str, Dict], mem_stats: Dict[str, Dict]) -> float:
    cs = coll_stats[base]
    ms = mem_stats[base]

    B_d, B_i = cs["B_d"], cs["B_i"]
    M_d, M_i = ms["M_d"], ms["M_i"]

    P_d = min(1.0, M_d / B_d) if B_d else 0.0
    P_i = min(1.0, M_i / B_i) if B_i else 0.0

    cost_idx = (Tm * P_i + Td * (1 - P_i)) / 2.0
    cost_data = (Tm * P_d + Td * (1 - P_d)) / 2.0
    return cost_idx + cost_data


def compute_read_cost(
    read_patterns: Dict[str, List[Tuple[str, int]]],
    workload_id: str,
    coll_stats: Dict[str, Dict],
    mem_stats: Dict[str, Dict],
) -> float:
    wl = WORKLOADS[workload_id]
    total = 0.0
    for qid, wq in wl["read_mix"].items():
        if wq == 0:
            continue
        cost_q = 0.0
        for base, n_acc in read_patterns[qid]:
            cost_q += n_acc * cost_rand(base, coll_stats, mem_stats)
        total += wq * cost_q
    return total


def compute_write_cost(
    write_patterns: Dict[str, List[Dict]],
    workload_id: str,
    coll_stats: Dict[str, Dict],
    mem_stats: Dict[str, Dict],
) -> float:
    wl = WORKLOADS[workload_id]
    total = 0.0
    for op_id, wop in wl["write_mix"].items():
        if wop == 0:
            continue
        cost_op = 0.0
        for step in write_patterns[op_id]:
            base = step["collection"]
            n_reads = float(step.get("reads", 0))
            n_writes = float(step.get("writes", 0))
            c = cost_rand(base, coll_stats, mem_stats)
            cost_op += n_reads * c + LAMBDA_WRITE * n_writes * c
        total += wop * cost_op
    return total


# =========================
# EVALUATION
# =========================

def evaluate_schema(flags: Dict[str, bool], vol_params: dict, wl_id: str) -> Dict:
    schema = flags_to_schema_name(flags)

    sizes = derive_avg_doc_sizes(flags, vol_params)
    coll_stats = estimate_coll_stats(sizes, vol_params)
    mem_stats = allocate_memory_blocks_per_schema(coll_stats)

    read_patterns = build_read_patterns(flags)
    write_patterns = build_write_patterns(flags, vol_params)

    alpha = WORKLOADS[wl_id]["alpha_read"]
    read_cost = compute_read_cost(read_patterns, wl_id, coll_stats, mem_stats)
    write_cost = compute_write_cost(write_patterns, wl_id, coll_stats, mem_stats)
    total_cost = alpha * read_cost + (1 - alpha) * write_cost

    return {
        "schema": schema,
        "alpha_read": alpha,
        "read_cost": read_cost,
        "write_cost": write_cost,
        "total_cost": total_cost,
    }


# =========================
# TABU SEARCH
# =========================

TABU_TENURE = 7
MAX_ITERATIONS = 80
MAX_NO_IMPROVEMENT = 20


def evaluate_with_cache(
    flags: Dict[str, bool],
    vol_params: dict,
    wl_id: str,
    eval_cache: Dict[Tuple[int, ...], Dict],
) -> Dict:
    key = flags_to_key(flags)
    cached = eval_cache.get(key)
    if cached is not None:
        return cached

    result = evaluate_schema(flags, vol_params, wl_id)
    eval_cache[key] = result
    return result


def tabu_search(
    vol_label: str,
    vol_params: dict,
    wl_id: str,
    tabu_tenure: int = TABU_TENURE,
    max_iterations: int = MAX_ITERATIONS,
    max_no_improvement: int = MAX_NO_IMPROVEMENT,
) -> Tuple[List[Dict], Dict]:
    start_time = time.perf_counter()

    eval_cache: Dict[Tuple[int, ...], Dict] = {}
    visited_solution_keys: Set[Tuple[int, ...]] = set()
    tabu_moves: Deque[str] = deque(maxlen=tabu_tenure)

    current_flags = make_base_flags()
    current_eval = evaluate_with_cache(current_flags, vol_params, wl_id, eval_cache)
    best_flags = copy_flags(current_flags)
    best_eval = dict(current_eval)

    visited_solution_keys.add(flags_to_key(current_flags))

    path_rows: List[Dict] = []
    path_rows.append({
        "volume": vol_label,
        "workload": wl_id,
        "iteration": 0,
        "move": "START",
        "move_is_tabu": False,
        "aspiration_used": False,
        "from_schema": None,
        "to_schema": current_eval["schema"],
        "accepted_cost": current_eval["total_cost"],
        "best_cost_so_far": best_eval["total_cost"],
        "delta_vs_current": None,
        "delta_vs_global_best": None,
        **current_eval,
    })

    iterations_without_improvement = 0

    for iteration in range(1, max_iterations + 1):
        best_candidate_flags: Optional[Dict[str, bool]] = None
        best_candidate_eval: Optional[Dict] = None
        best_candidate_move: Optional[str] = None
        best_candidate_is_tabu = False
        best_candidate_uses_aspiration = False

        for dupe_changed, neighbor_flags in generate_neighbors(current_flags):
            neighbor_eval = evaluate_with_cache(neighbor_flags, vol_params, wl_id, eval_cache)
            is_tabu = dupe_changed in tabu_moves
            aspiration = is_tabu and (neighbor_eval["total_cost"] < best_eval["total_cost"])

            if is_tabu and not aspiration:
                continue

            if best_candidate_eval is None or neighbor_eval["total_cost"] < best_candidate_eval["total_cost"]:
                best_candidate_flags = neighbor_flags
                best_candidate_eval = neighbor_eval
                best_candidate_move = dupe_changed
                best_candidate_is_tabu = is_tabu
                best_candidate_uses_aspiration = aspiration

        if best_candidate_eval is None or best_candidate_flags is None or best_candidate_move is None:
            break

        previous_cost = current_eval["total_cost"]
        current_flags = best_candidate_flags
        current_eval = best_candidate_eval
        current_key = flags_to_key(current_flags)
        visited_solution_keys.add(current_key)
        tabu_moves.append(best_candidate_move)

        improved_global = current_eval["total_cost"] < best_eval["total_cost"]
        if improved_global:
            best_flags = copy_flags(current_flags)
            best_eval = dict(current_eval)
            iterations_without_improvement = 0
        else:
            iterations_without_improvement += 1

        path_rows.append({
            "volume": vol_label,
            "workload": wl_id,
            "iteration": iteration,
            "move": best_candidate_move,
            "move_is_tabu": best_candidate_is_tabu,
            "aspiration_used": best_candidate_uses_aspiration,
            "from_schema": path_rows[-1]["to_schema"],
            "to_schema": current_eval["schema"],
            "accepted_cost": current_eval["total_cost"],
            "best_cost_so_far": best_eval["total_cost"],
            "delta_vs_current": current_eval["total_cost"] - previous_cost,
            "delta_vs_global_best": current_eval["total_cost"] - best_eval["total_cost"],
            **current_eval,
        })

        if iterations_without_improvement >= max_no_improvement:
            break

    execution_time_seconds = time.perf_counter() - start_time

    final_row = {
        "volume": vol_label,
        "workload": wl_id,
        "search_strategy": "tabu_search",
        "tabu_tenure": tabu_tenure,
        "max_iterations": max_iterations,
        "max_no_improvement": max_no_improvement,
        "iterations_executed": len(path_rows) - 1,
        "evaluated_candidates": len(eval_cache),
        "visited_solutions": len(visited_solution_keys),
        "execution_time_seconds": execution_time_seconds,
        **best_eval,
    }

    return path_rows, final_row


# =========================
# MAIN
# =========================

def main() -> Tuple[str, str]:
    path_rows_all = []
    final_rows = []

    last_wl_id = None

    for vol_label, vol_params in VOLUMES.items():
        for wl_id in WORKLOADS.keys():
            last_wl_id = wl_id
            path_rows, final_row = tabu_search(vol_label, vol_params, wl_id)
            path_rows_all.extend(path_rows)
            final_rows.append(final_row)

    if last_wl_id is None:
        raise ValueError("Nenhum workload definido em WORKLOADS.")

    df_path = pd.DataFrame(path_rows_all)
    df_final = pd.DataFrame(final_rows)

    alpha_pct = int(WORKLOADS[last_wl_id]["alpha_read"] * 100)

    out_path = f"costmodel_S3_tabu_search_path_R{alpha_pct}_W{100 - alpha_pct}.csv"
    out_final = f"costmodel_S3_tabu_search_final_R{alpha_pct}_W{100 - alpha_pct}.csv"

    df_path.sort_values(["volume", "workload", "iteration"]).to_csv(out_path, index=False)
    df_final.sort_values(["volume", "workload", "total_cost", "schema"]).to_csv(out_final, index=False)

    return out_path, out_final


if __name__ == "__main__":
    out_path, out_final = main()
    print(f"Arquivo de trajetória gerado: {out_path}")
    print(f"Arquivo final gerado: {out_final}")
