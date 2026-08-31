package com.demo.repo;

import java.util.List;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

@Repository
public interface OrderRepository {
    Order findById(String id);
    Order save(Order order);

    @Query(value = "SELECT o.* FROM orders o JOIN customers c ON o.cust_id = c.id "
                 + "WHERE o.status = ?1", nativeQuery = true)
    List<Order> findByStatus(String status);
}
