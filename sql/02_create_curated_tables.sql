-- =============================================================
-- 02_create_curated_tables.sql
-- Creates all five curated tables in fab_curated schema.
-- Run after 01_create_schemas.sql.
-- Python script 03_load_curated_to_mysql.py loads data into
-- these tables using pandas + SQLAlchemy (replace strategy).
-- =============================================================

USE fab_curated;

-- -------------------------------------------------------------
-- customer_master
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customer_master (
    customer_id                  VARCHAR(20)     NOT NULL,
    customer_name                VARCHAR(100)    NOT NULL,
    customer_segment             VARCHAR(50)     NOT NULL,
    industry                     VARCHAR(50)     NOT NULL,
    region                       VARCHAR(50)     NOT NULL,
    preferred_currency           VARCHAR(10)     NOT NULL,
    risk_category                VARCHAR(20)     NOT NULL,
    internal_rating              VARCHAR(10)     NOT NULL,
    relationship_tenure_years    DECIMAL(6, 2)   NOT NULL,
    relationship_status          VARCHAR(30)     NOT NULL,
    relationship_discount_pct    DECIMAL(8, 4)   NOT NULL,
    annual_revenue_aed           DECIMAL(22, 2)  NOT NULL,
    debt_to_equity_ratio         DECIMAL(8, 4)   NOT NULL,
    credit_score                 SMALLINT        NOT NULL,
    existing_exposure_aed        DECIMAL(22, 2)  NOT NULL,
    PRIMARY KEY (customer_id)
) ENGINE=InnoDB;

-- -------------------------------------------------------------
-- historical_deals
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS historical_deals (
    deal_id                      VARCHAR(20)     NOT NULL,
    deal_date                    DATE            NOT NULL,
    customer_id                  VARCHAR(20)     NOT NULL,
    product_id                   VARCHAR(20)     NOT NULL,
    product_type                 VARCHAR(50)     NOT NULL,
    currency                     VARCHAR(10)     NOT NULL,
    tenor                        VARCHAR(10)     NOT NULL,
    requested_amount             DECIMAL(22, 2)  NOT NULL,
    funding_cost_pct             DECIMAL(8, 4)   NOT NULL,
    standard_margin_pct          DECIMAL(8, 4)   NOT NULL,
    risk_premium_pct             DECIMAL(8, 4)   NOT NULL,
    relationship_discount_pct    DECIMAL(8, 4)   NOT NULL,
    recommended_price_pct        DECIMAL(8, 4)   NOT NULL,
    final_approved_price_pct     DECIMAL(8, 4)   NOT NULL,
    expected_margin_pct          DECIMAL(8, 4)   NOT NULL,
    deal_outcome                 VARCHAR(20)     NOT NULL,
    sales_channel                VARCHAR(50)     NOT NULL,
    PRIMARY KEY (deal_id),
    INDEX idx_deals_customer     (customer_id),
    INDEX idx_deals_product      (product_id),
    INDEX idx_deals_date         (deal_date)
) ENGINE=InnoDB;

-- -------------------------------------------------------------
-- pricing_policy
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pricing_policy (
    policy_id                               VARCHAR(20)    NOT NULL,
    customer_segment                        VARCHAR(50)    NOT NULL,
    product_type                            VARCHAR(50)    NOT NULL,
    risk_category                           VARCHAR(20)    NOT NULL,
    min_margin_pct                          DECIMAL(8, 4)  NOT NULL,
    risk_premium_pct                        DECIMAL(8, 4)  NOT NULL,
    max_relationship_discount_pct           DECIMAL(8, 4)  NOT NULL,
    approval_required_if_discount_above_pct DECIMAL(8, 4)  NOT NULL,
    min_expected_margin_pct                 DECIMAL(8, 4)  NOT NULL,
    rwa_risk_weight_pct                     DECIMAL(8, 2)  NOT NULL,
    status                                  VARCHAR(20)    NOT NULL,
    PRIMARY KEY (policy_id),
    INDEX idx_policy_lookup (customer_segment, product_type, risk_category)
) ENGINE=InnoDB;

-- -------------------------------------------------------------
-- product_master
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_master (
    product_id               VARCHAR(20)     NOT NULL,
    product_name             VARCHAR(100)    NOT NULL,
    product_type             VARCHAR(50)     NOT NULL,
    pricing_method           VARCHAR(20)     NOT NULL,
    currency                 VARCHAR(10)     NOT NULL,
    eligible_tenors          VARCHAR(100)    NOT NULL,
    standard_margin_pct      DECIMAL(8, 4)   NOT NULL,
    max_margin_pct           DECIMAL(8, 4)   NOT NULL,
    max_discount_allowed_pct DECIMAL(8, 4)   NOT NULL,
    min_ticket_size          DECIMAL(22, 2)  NOT NULL,
    max_ticket_size          DECIMAL(22, 2)  NOT NULL,
    eligible_segments        VARCHAR(100)    NOT NULL,
    PRIMARY KEY (product_id)
) ENGINE=InnoDB;

-- -------------------------------------------------------------
-- treasury_rate_sheet
-- -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS treasury_rate_sheet (
    rate_id             VARCHAR(20)    NOT NULL,
    currency            VARCHAR(10)    NOT NULL,
    benchmark_index     VARCHAR(20)    NOT NULL,
    tenor               VARCHAR(10)    NOT NULL,
    effective_date      DATE           NOT NULL,
    benchmark_rate_pct  DECIMAL(8, 4)  NOT NULL,
    funding_cost_pct    DECIMAL(8, 4)  NOT NULL,
    PRIMARY KEY (rate_id),
    INDEX idx_rate_lookup (currency, tenor, effective_date)
) ENGINE=InnoDB;
