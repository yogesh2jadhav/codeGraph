package com.etl;

import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.Row;
import org.apache.spark.sql.SparkSession;

public class DailyOrdersJob {

    public void run(SparkSession spark) {
        Dataset<Row> orders = spark.table("raw.orders");
        Dataset<Row> customers = spark.sql(
            "SELECT id, region FROM raw.customers WHERE active = true");

        Dataset<Row> enriched = orders
            .filter("status = 'PAID'")
            .join(customers, "id")
            .groupBy("region")
            .agg(org.apache.spark.sql.functions.sum("amount"))
            .withColumnRenamed("sum(amount)", "revenue")
            .orderBy("region");

        enriched.repartition(4)
            .write()
            .mode("overwrite")
            .saveAsTable("mart.daily_revenue");

        enriched.write().parquet("s3://bucket/daily/revenue.parquet");

        enriched.show();
        long n = enriched.count();
    }
}
