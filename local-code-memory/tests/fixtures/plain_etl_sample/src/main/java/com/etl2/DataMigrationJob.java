package com.etl2;

import java.sql.Connection;
import java.sql.DriverManager;
import java.util.List;

public class DataMigrationJob {

    public static void main(String[] args) throws Exception {
        DataMigrationJob job = new DataMigrationJob();
        job.run();
    }

    public void run() throws Exception {
        List<String> rows = extract();
        List<String> cleaned = transform(rows);
        load(cleaned);
    }

    private List<String> extract() {
        return List.of("a", "b");
    }

    private List<String> transform(List<String> rows) {
        return rows;
    }

    private void load(List<String> rows) throws Exception {
        try (Connection c = DriverManager.getConnection("jdbc:h2:mem:test")) {
            c.prepareStatement("INSERT INTO migrated_rows (value) VALUES (?)");
        }
    }
}
